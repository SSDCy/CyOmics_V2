import {canonicalJSON,freeze} from './sample-design-contract.mjs';
// R utils::write.table quote=TRUE, qmethod="double"; same strict field decoder
// as the original parseSampleQCTable. No ID splitting, trimming or statistical work.
export function parseDifferentialRowMap(bytes,version){
 const invalid=()=>{throw Error('differential_row_map_invalid');};
 if(!(bytes instanceof Uint8Array)||!bytes.length||bytes.length>26214400||bytes.at(-1)!==10||bytes.includes(0)||bytes.includes(13)||bytes[0]===239&&bytes[1]===187&&bytes[2]===191)invalid();
 let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{invalid();}
 let i=0;const records=[];
 while(i<text.length){const values=[];
  for(;;){let value;
   if(text[i]==='"'){i++;let start=i,closed=false;const parts=[];while(i<text.length){if(text[i]!=='"'){i++;continue;}parts.push(text.slice(start,i));if(text[i+1]==='"'){parts.push('"');i+=2;start=i;}else{i++;closed=true;break;}}if(!closed)invalid();value=parts.join('');}
   else{const start=i;while(i<text.length&&!['\t','\n'].includes(text[i])){if(text[i]==='"')invalid();i++;}value=text.slice(start,i);}
   values.push(value);if(values.length>4)invalid();if(text[i]==='\t'){i++;continue;}if(text[i]!=='\n')invalid();i++;break;
  }
  if(values.length!==4||records.length>10000)invalid();records.push(values);
 }
 const header=['source_record_number','protein_ids_raw','kept','work_row_ordinal'];
 if(!version||version.analysis_ready!==false||canonicalJSON(records.shift())!==canonicalJSON(header)||records.length!==version.source.row_count)invalid();
 const rows=[];for(const [index,r] of records.entries()){
  if(r[0]!==String(index+1)||!['TRUE','FALSE'].includes(r[2]))invalid();
  if(r[2]==='TRUE'){if(r[3]!==String(rows.length+1))invalid();rows.push({source_row_ordinal:index+1,protein_ids_raw:r[1]});}
  else if(r[3]!=='NA')invalid();
 }if(!rows.length)invalid();return freeze(rows);
}
