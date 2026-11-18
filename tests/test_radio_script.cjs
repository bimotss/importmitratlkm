const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const text = fs.readFileSync(path.join(__dirname, '../apps_script/RadioProgress.gs'), 'utf8');
const headers = ['NE ID','FE ID','HOP','Inhand Date','Tanggal Inhand','Flagging Progress',
  'Tanggal Finish Survey','Tanggal Finish DRM','Tanggal Finish Perizinan',
  'Tanggal Finish Material Preparation','Tanggal Material Onsite','Tanggal Finish Instalasi',
  'Tanggal Finish CT','Tanggal Finish UT','Tanggal Finish BAST','Tanggal DROP','Tanggal Plan DROP',
  'Tanggal HOLD','Durasi Hold (Days)','Tanggal Ganti Flagging','LAST UPDATE STATUS',
  'Aging Progress','Grouping Aging Progress','Progress Cut Off','Remark','Area'];
function make(rows = 2) {
  const data = [headers, ...Array.from({length:rows},()=>headers.map(()=>''))];
  const formulas = data.map(()=>headers.map(()=>'')), notes=data.map(()=>headers.map(()=>''));
  const context = vm.createContext({Date, Utilities:{formatDate:(d,tz)=>new Intl.DateTimeFormat('en-CA',{
    timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).format(d)},
    LockService:{getDocumentLock:()=>({waitLock(){},releaseLock(){}})}});
  vm.runInContext(text,context);
  const c = h => headers.indexOf(h)+1;
  const sheet = {getName:()=> 'Data',getLastColumn:()=>headers.length,
    getRange:(row,column,height=1,width=1)=>({
      getSheet:()=>sheet,getRow:()=>row,getColumn:()=>column,
      getLastRow:()=>row+height-1,getLastColumn:()=>column+width-1,
      getNumRows:()=>height,getNumColumns:()=>width,
      getValues:()=>data.slice(row-1,row-1+height).map(r=>r.slice(column-1,column-1+width)),
      getFormulas:()=>formulas.slice(row-1,row-1+height).map(r=>r.slice(column-1,column-1+width)),
      getNotes:()=>notes.slice(row-1,row-1+height).map(r=>r.slice(column-1,column-1+width)),
      setNumberFormat(){return this;},
      setValues(values){values.forEach((r,i)=>r.forEach((v,j)=>{data[row-1+i][column-1+j]=v;}));return this;},
      setNotes(values){values.forEach((r,i)=>r.forEach((v,j)=>{notes[row-1+i][column-1+j]=v;}));return this;}
    })};
  const set=(r,h,v)=>data[r-1][c(h)-1]=v;
  const get=(r,h)=>data[r-1][c(h)-1];
  const edit=(row,h,oldValue,height=1,width=1)=>context.onEdit({range:sheet.getRange(row,c(h),height,width),
    oldValue,source:{getSpreadsheetTimeZone:()=> 'Asia/Jakarta'}});
  return {context,set,get,edit,notes,formulas,c};
}
const statuses = vm.runInNewContext(text+'; JSON.stringify(MITRA_PROGRESS)');
for (const status of JSON.parse(statuses)) {
 test('radio status '+status,()=>{
  const f=make();f.set(2,'NE ID','N1');f.set(2,'Flagging Progress',status);
  f.edit(2,'Flagging Progress','');
  assert.equal(f.get(2,'LAST UPDATE STATUS'),status);
  assert.ok(f.get(2,'Tanggal Ganti Flagging') instanceof Date);
  assert.equal(f.get(2,'Tanggal Finish Survey'),'');
  if(status==='00. DROP') assert.ok(f.get(2,'Tanggal DROP') instanceof Date);
  if(status==='00. PLAN DROP') assert.ok(f.get(2,'Tanggal Plan DROP') instanceof Date);
  if(status==='00. HOLD') assert.ok(f.get(2,'Tanggal HOLD') instanceof Date);
 });
}
test('bulk paste closes prior stages, preserves formulas/cutoff and does not invent skipped dates',()=>{
 const f=make(17);const steps=JSON.parse(vm.runInContext('JSON.stringify(MITRA_FINISH)',f.context));
 Object.entries(steps).forEach(([status,header],i)=>{
  const row=i+2;f.set(row,'NE ID','N'+row);f.set(row,'Flagging Progress','14. Done BAST');
  f.notes[row-1][f.c('Flagging Progress')-1]='Note'+vm.runInContext('MITRA_NOTE',f.context)+JSON.stringify({progress:status});
  f.set(row,'Progress Cut Off','existing-cutoff');
  f.formulas[row-1][f.c('Aging Progress')-1]='=existingFormula';
  f.set(row,'Aging Progress',42);
 });
 f.edit(2,'Flagging Progress',undefined,Object.keys(steps).length);
 Object.entries(steps).forEach(([status,header],i)=>{
  assert.ok(f.get(i+2,header) instanceof Date);
  assert.equal(f.get(i+2,'Progress Cut Off'),'existing-cutoff');
  assert.equal(f.get(i+2,'Aging Progress'),42);
  assert.ok(f.notes[i+1][f.c('Flagging Progress')-1].startsWith('Note'));
 });
});
test('HOLD totals calendar days, adds episodes, and ignores duplicate event delivery',()=>{
 const f=make();const start=new Date(Date.now()-3*86400000);
 f.set(2,'NE ID','N1');f.set(2,'Flagging Progress','00. DROP');
 f.set(2,'Tanggal HOLD',start);f.set(2,'Durasi Hold (Days)',2);
 f.notes[1][f.c('Flagging Progress')-1]=vm.runInContext('MITRA_NOTE',f.context)+JSON.stringify({progress:'00. HOLD',holdStart:start.toISOString()});
 f.edit(2,'Flagging Progress','00. HOLD');assert.equal(f.get(2,'Durasi Hold (Days)'),5);
 f.edit(2,'Flagging Progress','00. HOLD');assert.equal(f.get(2,'Durasi Hold (Days)'),5);
});
test('Inhand follows ID edits; other dropdowns do not reset tracking',()=>{
 const f=make();f.set(2,'NE ID','N1');f.edit(2,'NE ID','');
 assert.ok(f.get(2,'Tanggal Inhand') instanceof Date);
 assert.ok(f.get(2,'Inhand Date') instanceof Date);
 f.set(2,'Area','Area 1');f.edit(2,'Area','Area 2');
 assert.equal(f.get(2,'Tanggal Ganti Flagging'),'');
});
