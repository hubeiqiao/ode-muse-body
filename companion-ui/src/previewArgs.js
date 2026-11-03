export function previewArguments(cardId,targetHandle){
 const card=typeof cardId==='string'?cardId.trim():'';
 const target=typeof targetHandle==='string'?targetHandle.trim():'';
 if(!/^\d{8}-\d{2}$/.test(card))throw new Error('Enter the visible card ID, for example YYYYMMDD-01.');
 if(!/^@[A-Za-z0-9_]{1,15}$/.test(target))throw new Error('Enter its exact target handle, starting with @.');
 return {mode:'preview',card_id:card,target_handle:target};
}
