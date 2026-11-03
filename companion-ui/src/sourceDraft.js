// Source selection is explicit and scoped to the current API boot.
export function checkedSourceFromReceipt(receipt,serverInstance){
 const value=receipt?.result;
 if(receipt?.historical===true||typeof receipt?.request_id!=='string'||!receipt.request_id||!serverInstance||receipt?.server_instance!==serverInstance||receipt?.tool_name!=='public.read_results'||receipt?.status!=='completed'
    ||value?.verification_policy!=='app_public_result_quotes'||value?.verification_passed!==true
    ||value?.source_kind!=='verified_search_snapshot'||!Array.isArray(value?.results)||!value.results.length)return null;
 return {request_id:receipt.request_id,app:value.app,query:value.query};
}
export function draftArguments(brief,source,useSource){
 if(!useSource)return {goal:brief};
 if(!source?.request_id)throw new Error('Read and check a public search excerpt first.');
 return {goal:brief,source_read_request_id:source.request_id};
}
export function draftReviewText(result){
 let text=result.body+'\n\nPrepared locally for review. Not published.';
 const source=result.source_evidence;
 if(source)text+='\n\nChecked search snapshot: '+source.app+' / '+source.query+'\n'+source.results.map(r=>'“'+r.title_quote+'”'+(r.byline_quote?' | '+r.byline_quote:'')).join('\n')+'\nPublic content, not verified customer feedback. Business interpretation needs review.';
 return text;
}
