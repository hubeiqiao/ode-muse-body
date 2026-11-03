import asyncio
import json
import io
import base64
from PIL import Image

import httpx
import pytest

from hidagent.decision import DecisionUnavailable, Jev, read_key_file


def box_answer(confidence=.95):
    return {'type': 'box', 'box': {'left': .5, 'right': .7, 'top': .1, 'bottom': .2}, 'confidence': confidence}


def run_response(answers, status=200):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={'model': 'jev-1.14.0', 'answers': answers})
    async def run():
        client = Jev('synthetic-key', transport=httpx.MockTransport(handle))
        try:
            image=io.BytesIO();Image.new('RGB',(498,1080)).save(image,format='JPEG')
            return await client.locate(image.getvalue(), 'Settings icon')
        finally:
            await client.close()
    return run, calls


def test_images_referenced_once_and_point_is_fractional():
    run, calls = run_response({'visible': {'type': 'noul', 'noul': 1}, 'target': box_answer()})
    result = asyncio.run(run())
    assert result['point'] == pytest.approx({'x': .6, 'y': .15})
    request = calls[0]
    assert str(request.url) == 'https://api.typesafe.ai/v1/systemone'
    assert request.headers['TypeSafe-Danger-Zone-Images'] == 'true'
    payload = json.loads(request.content)
    assert payload['state']['screen'] in payload['assets']
    assert len(payload['assets']) == 1
    assert payload['state']['context']['target'] == 'Settings icon'


@pytest.mark.parametrize('change', ['confidence', 'reverse', 'nan', 'missing', 'invisible'])
def test_uncertain_or_malformed_location_cannot_be_used(change):
    answers = {'visible': {'type': 'noul', 'noul': 1}, 'target': box_answer()}
    if change == 'confidence': answers['target']['confidence'] = .6
    if change == 'reverse': answers['target']['box']['left'] = .9
    if change == 'nan': answers['target']['box']['top'] = 'NaN'
    if change == 'missing': del answers['target']
    if change == 'invisible': answers['visible']['noul'] = .1
    run, _ = run_response(answers)
    with pytest.raises(DecisionUnavailable): asyncio.run(run())


def test_provider_failure_has_no_retry_or_body_leak():
    run, calls = run_response({}, 429)
    with pytest.raises(DecisionUnavailable, match='Jev HTTP 429'): asyncio.run(run())
    assert len(calls) == 1


def test_key_file_reads_only_named_key_without_executing(tmp_path):
    p = tmp_path / 'local.env'
    p.write_text("OTHER_KEY=do-not-use\nexport TYPESAFE_API_KEY='synthetic-key' # comment\necho never-execute\n")
    assert read_key_file(str(p)) == 'synthetic-key'


def test_choice_must_be_one_of_supplied_actions():
    async def run():
        def handle(request):
            return httpx.Response(200, json={'answers': {'next': {'type': 'choice', 'choice': 'invented', 'confidence': 1}}})
        client = Jev('synthetic-key', transport=httpx.MockTransport(handle))
        try:
            await client.evaluate('test', {'next': {'type': 'choice', 'instructions': 'Next?', 'criteria': {'wait': 'Wait.'}}})
        finally:
            await client.close()
    with pytest.raises(DecisionUnavailable): asyncio.run(run())


def test_large_ipad_frame_is_whole_aspect_preserved_and_boxes_stay_fractional():
    from hidagent.decision import compatible_image
    image=io.BytesIO();Image.new('RGB',(1646,1080)).save(image,format='JPEG')
    payload,geometry=compatible_image(image.getvalue())
    assert Image.open(io.BytesIO(payload)).size==(1080,709)
    assert geometry=={'source':(1646,1080),'sent':(1080,709)}
    async def run():
        def handle(request):
            body=json.loads(request.content);asset=next(iter(body['assets'].values()))
            assert Image.open(io.BytesIO(base64.b64decode(asset['data_base64']))).size==(1080,709)
            return httpx.Response(200,json={'answers':{'target':box_answer(),'visible':{'type':'noul','noul':1}}})
        client=Jev('test',transport=httpx.MockTransport(handle))
        try:
            result=await client.locate(image.getvalue(),'test target')
            assert result['point']==pytest.approx({'x':.6,'y':.15})
        finally:await client.close()
    asyncio.run(run())


def test_phone_and_portrait_ipad_jpeg_bytes_are_not_reencoded():
    from hidagent.decision import compatible_image
    for size in [(498,1080),(710,1080)]:
        image=io.BytesIO();Image.new('RGB',size).save(image,format='JPEG');raw=image.getvalue()
        payload,geometry=compatible_image(raw)
        assert payload is raw and geometry['source']==geometry['sent']==size


@pytest.mark.parametrize('size',[(710,63),(80,400),(32,32),(3000,100),(100,3000)])
def test_skinny_or_small_image_is_padded_into_official_jev_bounds(size):
    from hidagent.decision import compatible_image
    buf=io.BytesIO();Image.new('RGB',size,'white').save(buf,format='JPEG')
    encoded,g=compatible_image(buf.getvalue());actual=Image.open(io.BytesIO(encoded)).size
    assert all(280<=v<=1568 for v in actual)
    assert g['source']==size and g['sent']==actual
    assert 'content_box' in g


def test_padded_box_is_returned_in_original_crop_coordinates():
    from hidagent.decision import compatible_image
    buf=io.BytesIO();Image.new('RGB',(710,63),'white').save(buf,format='JPEG');image=buf.getvalue()
    _,g=compatible_image(image)
    assert 'content_box' in g
    c=g['content_box'];original={'left':.2,'right':.8,'top':.25,'bottom':.75}
    sent={k:c['left']+original[k]*(c['right']-c['left']) if k in ('left','right') else c['top']+original[k]*(c['bottom']-c['top']) for k in original}
    async def run():
        def handle(request):
            body=json.loads(request.content);assert body['state']['screen_content_box']==c
            return httpx.Response(200,json={'answers':{'target':{'type':'box','box':sent,'confidence':.99}}})
        client=Jev('synthetic',transport=httpx.MockTransport(handle))
        try:r=await client.evaluate({}, {'target':{'type':'box','instructions':'Locate actual content'}},image)
        finally:await client.close()
        assert r['answers']['target']['box']==pytest.approx(original)
    asyncio.run(run())


@pytest.mark.parametrize('bounds',[(0,.1),(.3,.6)])
def test_box_wholly_or_partly_in_padding_is_rejected_not_clipped(bounds):
    buf=io.BytesIO();Image.new('RGB',(710,63),'white').save(buf,format='JPEG')
    async def run():
        def handle(request):
            return httpx.Response(200,json={'answers':{'target':{'type':'box','box':{'left':.2,'right':.8,'top':bounds[0],'bottom':bounds[1]},'confidence':.99}}})
        client=Jev('synthetic',transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(DecisionUnavailable):await client.evaluate({}, {'target':{'type':'box','instructions':'Locate'}},buf.getvalue())
        finally:await client.close()
    asyncio.run(run())
