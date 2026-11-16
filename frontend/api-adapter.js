/** Production adapter contract. Not enabled until verified Supabase session and FastAPI endpoint exist. */
export class ProteomicsAPI {
  constructor({baseURL, getVerifiedSession}) {if(!baseURL?.startsWith('https://'))throw new Error('HTTPS API endpoint required');this.baseURL=baseURL.replace(/\/$/,'');this.getVerifiedSession=getVerifiedSession;}
  async request(path,{method='GET',body,signal}={}) {
    const session=await this.getVerifiedSession();
    if(!session?.access_token||!session?.user?.email_confirmed_at)throw new Error('VERIFIED_EMAIL_REQUIRED');
    if(session.expires_at*1000<=Date.now())throw new Error('SESSION_EXPIRED');
    const response=await fetch(this.baseURL+'/api/v1'+path,{method,signal,headers:{Authorization:'Bearer '+session.access_token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
    if(response.status===401)throw new Error('SESSION_EXPIRED');
    if(!response.ok)throw new Error('API_ERROR_'+response.status);
    return response.status===204?null:response.json();
  }
  projects(){return this.request('/projects');}
  createProject(input){return this.request('/projects',{method:'POST',body:input});}
  tasks(projectId){return this.request('/projects/'+encodeURIComponent(projectId)+'/tasks');}
  submit(projectId,input){return this.request('/projects/'+encodeURIComponent(projectId)+'/tasks',{method:'POST',body:input});}
  cancel(projectId,taskId){return this.request('/projects/'+encodeURIComponent(projectId)+'/tasks/'+encodeURIComponent(taskId)+'/cancel',{method:'POST'});}
  protein(projectId,proteinId){return this.request('/projects/'+encodeURIComponent(projectId)+'/proteins/'+encodeURIComponent(proteinId));}
}
