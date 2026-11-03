'use strict';
const {parseTweet}=require('twitter-text');
let chunks=[],size=0;
process.stdin.on('data',chunk=>{size+=chunk.length;if(size>16384)process.exit(2);chunks.push(chunk);});
process.stdin.on('end',()=>{try{const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!input||Object.keys(input).length!==1||typeof input.text!=='string'||Array.from(input.text).length>1500)process.exit(2);const result=parseTweet(input.text);process.stdout.write(JSON.stringify({valid:result.valid,weighted_length:result.weightedLength,remaining:280-result.weightedLength,library:'twitter-text@3.1.0'}));}catch{process.exit(2);}});
