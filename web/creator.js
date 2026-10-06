'use strict';
const $=id=>document.getElementById(id);
const element=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
let notebooks=[],token='',nameEdited=false;
function render(){
  const query=$('search').value.toLowerCase().trim();
  const matching=notebooks.filter(book=>(book.target+' '+book.path).toLowerCase().includes(query));
  $('notebook-count').textContent=notebooks.length;$('notebooks').replaceChildren();
  if(!matching.length){const empty=element('div','empty');empty.append(element('b','',query?'No matching notebooks':'Your first target starts here.'),element('span','',query?'Try another target name.':'Create a notebook to map functionalities and track your checks.'));$('notebooks').append(empty);return;}
  matching.forEach(book=>{
    const card=element('a','notebook');card.href='/'+book.path.split('/').map(encodeURIComponent).join('/');
    const header=element('div','notebook-head'),heading=element('div');heading.append(element('h3','',book.target),element('div','notebook-path',book.database));header.append(heading,element('span','arrow','↗'));card.append(header);
    const counts=element('div','counts');[[book.features,'functionalities'],[book.recaps,'recaps'],[book.openChecks,'next checks']].forEach(([number,label])=>{const node=element('span');node.append(element('b','',number),document.createTextNode(label));counts.append(node);});card.append(counts);
    if(book.error)card.append(element('p','error',book.error));
    else if(book.updatedAt){const date=new Date(book.updatedAt);if(!Number.isNaN(date.valueOf()))card.append(element('p','updated','Last saved '+date.toLocaleString()));}
    $('notebooks').append(card);
  });
}
async function loadLibrary(){
  $('refresh').disabled=true;$('load-error').hidden=true;
  try{const response=await fetch('/api/notebooks',{cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load notebooks.');notebooks=data.notebooks;token=data.token;render();}
  catch(error){$('load-error').textContent=error.message+' Check that the local launcher is running.';$('load-error').hidden=false;}
  finally{$('refresh').disabled=false;}
}
$('search').addEventListener('input',render);
$('refresh').addEventListener('click',loadLibrary);
$('name').addEventListener('input',()=>nameEdited=true);
$('target').addEventListener('input',()=>{if(!nameEdited)$('name').value=$('target').value.toLowerCase().replace(/^https?:\/\//,'').replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);});
$('create-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!$('create-form').reportValidity())return;
  const payload={name:$('name').value.trim(),target:$('target').value.trim(),programUrl:$('program-url').value.trim()};
  $('create').disabled=true;$('create').textContent='Creating…';$('create-error').hidden=true;
  try{
    if(!token)await loadLibrary();
    const send=()=>fetch('/api/notebooks',{method:'POST',headers:{'Content-Type':'application/json','X-Notebook-Token':token},body:JSON.stringify(payload)});
    let response=await send(),data=await response.json();
    if(response.status===403){await loadLibrary();response=await send();data=await response.json();}
    if(!response.ok)throw new Error(data.error||'Could not create this notebook.');
    window.location.assign('/'+data.path.split('/').map(encodeURIComponent).join('/'));
  }catch(error){$('create-error').textContent=error.message;$('create-error').hidden=false;}
  finally{$('create').disabled=false;$('create').textContent='Create & open notebook ↗';}
});
loadLibrary();
