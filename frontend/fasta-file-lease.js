// Memory ownership only. No content reads, format validation, storage or I/O.
// The host supplies a synchronous, trusted, non-reused context revision key.
export function createFastaFileLeaseController({enabled=false,getContextKey=null,FileImpl=globalThis.File}={}){
  const active=enabled===true;
  const validKey=key=>typeof key==='string'&&key.length>=1&&key.length<=512&&!/[^\x21-\x7e]/.test(key);
  let disposed=false,selected=null,revision={},code=null,readingContext=false;

  function drop(reason=null){
    if(selected){selected.file=null;selected.key=null;selected.valid=false;}
    selected=null;revision={};code=reason;
  }
  function contextKey(){
    if(readingContext||typeof getContextKey!=='function')return null;
    readingContext=true;
    try{
      const key=getContextKey();
      if(validKey(key))return key;
      // An accidental native Promise is invalid, never an asynchronous authority.
      // Consume its rejection without inspecting arbitrary thenable properties.
      if(key&&typeof key==='object'){try{Promise.prototype.then.call(key,()=>{},()=>{});}catch{}}
      return null;
    }catch{return null;}finally{readingContext=false;}
  }
  function bound(cell,key){
    if(!active||disposed||!cell||selected!==cell||!cell.valid)return false;
    const before=revision,current=contextKey();
    if(before!==revision||selected!==cell||!cell.valid)return false;
    if(current===null||current!==cell.key){drop('context_unavailable');return false;}
    return validKey(key)&&key===current;
  }
  function nativeFile(file){
    // Read and discard the intrinsic name solely for a runtime File brand check.
    // No contents, own getters, extension/size checks or retained filename.
    // Some runtimes accept transparent File proxies: this is not a security check.
    try{const getter=Object.getOwnPropertyDescriptor(FileImpl?.prototype,'name')?.get;return typeof getter==='function'&&typeof getter.call(file)==='string';}catch{return false;}
  }
  function select(file,key){
    if(!active||disposed)return false;
    drop();
    if(!validKey(key)){code='context_unavailable';return false;}
    const before=revision,current=contextKey();
    if(before!==revision||selected!==null)return false;
    if(current!==key){code='context_unavailable';return false;}
    const valid=nativeFile(file);
    if(before!==revision||selected!==null)return false;
    if(!valid){code='invalid_file_reference';return false;}
    const again=contextKey();
    if(before!==revision||selected!==null)return false;
    if(again!==current){code='context_unavailable';return false;}
    selected={file,key:current,valid:true};code=null;return true;
  }
  function acquire(key){
    const cell=selected;
    if(!bound(cell,key))return null;
    let closed=false;
    return Object.freeze({
      getFile(currentKey){return !closed&&bound(cell,currentKey)?cell.file:null;},
      isCurrent(currentKey){return !closed&&bound(cell,currentKey);},
      complete(currentKey){if(closed)return false;const accepted=bound(cell,currentKey);closed=true;return accepted;},
      cancel(){if(closed)return false;closed=true;return true;}
    });
  }
  function clear(){if(disposed)return false;const had=selected!==null;drop();return had;}
  function invalidate(){if(disposed)return false;const had=selected!==null;drop('context_unavailable');return had;}
  function dispose(){if(disposed)return false;disposed=true;drop();return true;}
  function status(){if(selected)bound(selected,selected.key);return Object.freeze({phase:disposed?'disposed':!active?'disabled':selected?'selected':'empty',hasSelection:!disposed&&active&&selected!==null,code:disposed||!active?null:code});}
  return Object.freeze({select,acquire,clear,invalidate,dispose,status});
}
