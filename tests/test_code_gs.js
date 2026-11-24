// Runs Code.gs against a small pretend Google (mock). Checks secret, routing, sheet/tab/write logic.
const fs = require('fs'), assert = require('assert');
const grid = {}; const files = [];
function mkRange(sh, r, c, nr, nc) {
  return { setValues(v){ for(let i=0;i<v.length;i++)for(let j=0;j<v[i].length;j++){sh.data[(r+i)+','+(c+j)]=v[i][j];} sh._lr=Math.max(sh._lr,r+v.length-1); sh._lc=Math.max(sh._lc,c+v[0].length-1); return this;},
    setValue(v){sh.data[r+','+c]=v; sh._lr=Math.max(sh._lr,r); sh._lc=Math.max(sh._lc,c); return this;},
    getDisplayValues(){const o=[];for(let i=0;i<nr;i++){const row=[];for(let j=0;j<nc;j++)row.push(sh.data[(r+i)+','+(c+j)]||'');o.push(row);}return o;},
    setFontWeight(){return this},setBackground(){return this},setWrap(){return this},createFilter(){return this},setNote(){return this},
    setNumberFormat(){return this},setDataValidation(){sh.validation=true;return this} };
}
function mkSheet(name){const sh={name,data:{},_lr:0,_lc:0,rules:0,frozen:0,
  getName(){return this.name},setName(n){this.name=n},getLastRow(){return this._lr},getLastColumn(){return this._lc},
  getMaxRows(){return 1000},getMaxColumns(){return 40},insertColumnsAfter(){},insertRowsAfter(){},setFrozenRows(n){this.frozen=n},
  getRange(r,c,nr=1,nc=1){return mkRange(this,r,c,nr,nc)},setColumnWidth(){},setConditionalFormatRules(x){this.rules=x.length}};return sh;}
const SpreadsheetApp={
  create(name){const id='id'+Object.keys(grid).length;grid[id]={name,sheets:[mkSheet('Sheet1')]};return ss(id)},
  openById(id){if(!grid[id])throw new Error('nope');return ss(id)},
  newDataValidation(){const b={requireValueInList(){return b},setAllowInvalid(){return b},build(){return {}}};return b},
  newConditionalFormatRule(){const b={whenTextEqualTo(){return b},setBackground(){return b},setRanges(){return b},build(){return {}}};return b},
  flush(){}};
function ss(id){return {getId:()=>id,getUrl:()=>'https://docs.google.com/spreadsheets/d/'+id,getSheets:()=>grid[id].sheets,
  getSheetByName:n=>grid[id].sheets.find(s=>s.name===n)||null,insertSheet(n){const s=mkSheet(n);grid[id].sheets.push(s);return s}}}
const folder={getName:()=> 'Expo Sheets',addFile(f){files.push(f)},getFilesByName(n){const l=files.filter(f=>f.name===n);let i=0;return{hasNext:()=>i<l.length,next:()=>l[i++]}},
  getFilesByType(){let i=0;return{hasNext:()=>i<files.length,next:()=>files[i++]}}};
const DriveApp={getFolderById:()=>folder,getFileById(id){return{id,name:grid[id].name,getId:()=>id,getUrl:()=>'u'+id,isTrashed:()=>false,getMimeType:()=> 'application/vnd.google-apps.spreadsheet'}},
  getRootFolder:()=>({removeFile(){}})};
// files list objects need methods
const origAdd=folder.addFile; folder.addFile=function(f){files.push({name:f.name,getName:()=>f.name,getId:()=>f.id,getUrl:()=>'u'+f.id,isTrashed:()=>false,getMimeType:()=> 'application/vnd.google-apps.spreadsheet'})};
const props={SECRET:'pw123',FOLDER_ID:'F'};
const ctx={SpreadsheetApp,DriveApp,MimeType:{GOOGLE_SHEETS:'application/vnd.google-apps.spreadsheet',GOOGLE_DOCS:'d',GOOGLE_SLIDES:'s',PDF:'p'},
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k]})},
  ContentService:{MimeType:{JSON:1},createTextOutput:t=>({t,setMimeType(){return this}})},
  LockService:{getScriptLock:()=>({waitLock(){},releaseLock(){}})},Utilities:{base64Encode:()=>'x'}};
const code=fs.readFileSync(__dirname+'/../apps_script/Code.gs','utf8');
const run=new Function(...Object.keys(ctx),code+';return {doPost,doGet};')(...Object.values(ctx));
const call=(o)=>JSON.parse(run.doPost({postData:{contents:JSON.stringify(o)}}).t);
// the real writing code needs files with name; patch DriveApp.getFileById to return name
DriveApp.getFileById=id=>({id,name:grid[id].name});
assert.strictEqual(call({secret:'bad',action:'ping'}).ok,false);
assert.strictEqual(call({secret:'pw123',action:'ping'}).ok,true);
assert.strictEqual(call({secret:'pw123',action:'zzz'}).ok,false);
const a=call({secret:'pw123',action:'ensure_sheet',name:'Smart: Expo'}); assert(a.ok&&a.created);
const b=call({secret:'pw123',action:'ensure_sheet',name:'Smart  Expo'}); assert(b.ok&&!b.created&&b.id===a.id);
assert.strictEqual(call({secret:'pw123',action:'list_sheets'}).sheets.length,1);
const H=['Company Name','Email','Hard Copy Brochure','Extra'];
assert(call({secret:'pw123',action:'ensure_tab',sheetId:a.id,tab:'2026',headers:H,manual:'Hard Copy Brochure'}).created);
assert(!call({secret:'pw123',action:'ensure_tab',sheetId:a.id,tab:'2026',headers:H,manual:'Hard Copy Brochure'}).created);
assert.deepStrictEqual(call({secret:'pw123',action:'list_tabs',sheetId:a.id}).tabs,['2026']);
call({secret:'pw123',action:'write_tab',sheetId:a.id,tab:'2026',edits:[],appends:[['A Ltd','a@a.com','','' ],['B Ltd','b@b.com','','']],startRow:2});
call({secret:'pw123',action:'write_tab',sheetId:a.id,tab:'2026',edits:[[3,1,'new@b.com']],appends:[],startRow:4});
const t=call({secret:'pw123',action:'read_tab',sheetId:a.id,tab:'2026'});
assert.deepStrictEqual(t.header,H); assert.strictEqual(t.rows.length,2); assert.strictEqual(t.rows[1][1],'new@b.com');
assert(grid[a.id].sheets[0].validation && grid[a.id].sheets[0].rules===1 && grid[a.id].sheets[0].frozen===1);
const x=call({secret:'pw123',action:'read_external',url:'https://docs.google.com/spreadsheets/d/'+a.id+'/edit'});
assert(x.ok&&x.sheets[0].values.length===3);
assert.strictEqual(call({secret:'pw123',action:'read_external',url:'hello'}).ok,false);
console.log('Code.gs mock test: all passed');
