// Import-only synthetic seam. A real AuthClient constructor must never execute in these tests.
export async function resolve(specifier,context,next){
  if(specifier==='@supabase/auth-js')return next(new URL('./sample-sdk-import-standin.mjs',import.meta.url).href,context);
  return next(specifier,context);
}
