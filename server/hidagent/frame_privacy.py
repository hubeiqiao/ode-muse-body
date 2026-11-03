"""Local deny-only checks for known excluded system overlays.

This is deliberately not a general privacy classifier. Only UI-only templates
from actually observed layouts are recognized. No OCR, remote calls, capture,
HID or private-content storage. Unknown layouts still need operator review.
"""
import io
import logging
import os
from PIL import Image
from .app_controls import public_search_control


def privacy_template_receipt(image):
    """Coverage metadata only. A supported size is never a privacy approval."""
    if not image:return {}
    try:
        with Image.open(io.BytesIO(image)) as picture:size=picture.size
    except (OSError,ValueError,TypeError):return {'privacy_templates':'invalid_image'}
    return {'privacy_templates':('deny_only_known_layouts' if size==(710,1080)
                                 else 'unavailable_for_geometry')}


def known_excluded_overlay(image):
    if not image:return None
    if public_search_control(image,'System UI',{'device':'ipad','orientation':'portrait'},
            control='excluded_notification_overlay'):
        return 'notification_center_known_layout'
    if public_search_control(image,'System UI',{'device':'ipad','orientation':'portrait'},control='excluded_password_fill'):
        return 'password_fill_known_layout'
    for app in ('chatgpt','grok'):
        if public_search_control(image,'System UI',{'device':'ipad','orientation':'portrait'},control='excluded_'+app+'_sidebar'):
            return app+'_history_sidebar_known_layout'
    return None


def check_camera_frame(image):
    """Jev/Haiku/Meta never receive an image while a camera conversation is active,
    nor any frame recognised as Camera UI (session or not). Inert when no camera
    session exists and no Camera template matches (CAMERA_CONVERSATION off)."""
    if not image:return
    from . import camera_conversation
    if camera_conversation.active_any():
        from .decision import DecisionUnavailable
        raise DecisionUnavailable('Camera conversation active; device image withheld, no model request sent')
    # The iPad is the AI's own device; the owner allows Camera UI frames (2026-11-01).
    # WITHHOLD_CAMERA_UI=1 restores the old refusal.
    if os.environ.get('WITHHOLD_CAMERA_UI', '0') == '1' and camera_conversation.recognised_camera_ui(image):
        from .decision import DecisionUnavailable
        raise DecisionUnavailable('Camera UI visible; device image withheld, no model request sent')


def check_cloud_frame(image):
    check_camera_frame(image)
    receipt=privacy_template_receipt(image)
    if receipt.get('privacy_templates')=='unavailable_for_geometry':
        # Always visible even without an enabled Trace. Never log image content.
        logging.getLogger(__name__).warning('Cloud frame check: privacy_templates=unavailable_for_geometry',extra=receipt)
    if known_excluded_overlay(image):
        from .decision import DecisionUnavailable
        raise DecisionUnavailable('Excluded private/system layout; cloud image withheld, no model request sent')
