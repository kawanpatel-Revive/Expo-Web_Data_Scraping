/**
 * Expo Data Tool - Google Apps Script (the "hands" of the tool).
 * It only creates / reads / writes Google Sheets inside ONE Drive folder.
 * No data is sent anywhere else. Every request must carry the secret password.
 *
 * Script properties (Project Settings > Script properties):
 *   SECRET    = your own secret password
 *   FOLDER_ID = ID of the Drive folder where the Sheets live
 */
// Keep every data row one line high; long text is clipped (full text stays in the cell).
function tidy_(sh) {
  var lr = sh.getLastRow(), lc = sh.getLastColumn();
  if (lr < 2 || lc < 1) return;
  var rng = sh.getRange(2, 1, lr - 1, lc);
  rng.setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP).setVerticalAlignment('middle');
  sh.setRowHeightsForced(2, lr - 1, 24);
}

function props_() { return PropertiesService.getScriptProperties(); }

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function doGet() { return out_({ ok: true, info: 'Expo tool script is running. Use POST.' }); }

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    var secret = props_().getProperty('SECRET');
    if (!secret || req.secret !== secret) return out_({ ok: false, error: 'Wrong secret password.' });
    var folderId = props_().getProperty('FOLDER_ID');
    if (!folderId) return out_({ ok: false, error: 'FOLDER_ID is not set in Script properties.' });
    var fn = ACTIONS[req.action];
    if (!fn) return out_({ ok: false, error: 'Unknown action: ' + req.action });
    var res = fn(req, DriveApp.getFolderById(folderId));
    res.ok = true;
    return out_(res);
  } catch (err) {
    return out_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

var ACTIONS = {
  ping: function (r, folder) { return { folder: folder.getName() }; },

  list_sheets: function (r, folder) {
    var it = folder.getFilesByType(MimeType.GOOGLE_SHEETS), res = [];
    while (it.hasNext()) { var f = it.next(); if (!f.isTrashed()) res.push({ id: f.getId(), name: f.getName(), url: f.getUrl() }); }
    res.sort(function (a, b) { return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1; });
    return { sheets: res };
  },

  ensure_sheet: function (r, folder) {
    var name = String(r.name || '').replace(/[\[\]\\\/*?:]/g, ' ').trim().substring(0, 100);
    if (!name) throw new Error('Sheet name is empty.');
    var it = folder.getFilesByName(name);
    while (it.hasNext()) {
      var f = it.next();
      if (!f.isTrashed() && f.getMimeType() === MimeType.GOOGLE_SHEETS) return { id: f.getId(), url: f.getUrl(), created: false };
    }
    var ss = SpreadsheetApp.create(name);
    var file = DriveApp.getFileById(ss.getId());
    try { file.moveTo(folder); }                       // works for normal and shared folders
    catch (e1) {
      folder.addFile(file);                            // older way
      try { DriveApp.getRootFolder().removeFile(file); } catch (e2) { /* may stay in My Drive too: fine */ }
    }
    return { id: ss.getId(), url: ss.getUrl(), created: true };
  },

  list_tabs: function (r) {
    return { tabs: SpreadsheetApp.openById(r.sheetId).getSheets().map(function (s) { return s.getName(); }) };
  },

  ensure_tab: function (r) {
    var ss = SpreadsheetApp.openById(r.sheetId), tab = String(r.tab), H = r.headers;
    var sh = ss.getSheetByName(tab);
    if (sh) return { created: false };
    var only = ss.getSheets().length === 1 ? ss.getSheets()[0] : null;
    if (only && only.getLastRow() === 0 && only.getLastColumn() <= 1 && /^(Sheet1|Sheet 1)$/i.test(only.getName())) {
      sh = only; sh.setName(tab);
    } else { sh = ss.insertSheet(tab); }
    var n = H.length;
    if (sh.getMaxColumns() < n) sh.insertColumnsAfter(sh.getMaxColumns(), n - sh.getMaxColumns());
    sh.getRange(1, 1, 1, n).setValues([H]).setFontWeight('bold').setBackground('#d9e2f3').setWrap(true);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, n).createFilter();
    sh.getRange(1, 1, 1, n).setNote('Please do not rename or delete these headings. The tool finds columns by heading name.');
    // plain text format for data (phone numbers, booth numbers, codes stay as typed)
    sh.getRange(2, 1, sh.getMaxRows() - 1, n).setNumberFormat('@');
    // Hard Copy Brochure: Yes / No dropdown, green when Yes
    var m = H.indexOf(r.manual) + 1;
    if (m > 0) {
      var rng = sh.getRange(2, m, sh.getMaxRows() - 1, 1);
      rng.setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(['Yes', 'No'], true).setAllowInvalid(false).build());
      var rule = SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('Yes').setBackground('#b7e1cd').setRanges([rng]).build();
      sh.setConditionalFormatRules([rule]);
    }
    for (var c = 1; c <= n; c++) sh.setColumnWidth(c, c <= 3 ? 220 : 160);
    // every row one line high; long text is cut at the cell edge (the formula bar shows all of it)
    sh.getRange(2, 1, sh.getMaxRows() - 1, n).setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP).setVerticalAlignment('middle');
    sh.setRowHeightsForced(2, sh.getMaxRows() - 1, 24);
    return { created: true };
  },

  read_tab: function (r) {
    var sh = SpreadsheetApp.openById(r.sheetId).getSheetByName(r.tab);
    if (!sh) throw new Error('Tab not found: ' + r.tab);
    var lr = sh.getLastRow(), lc = sh.getLastColumn();
    if (lr < 1 || lc < 1) return { header: [], rows: [] };
    var vals = sh.getRange(1, 1, lr, lc).getDisplayValues();
    return { header: vals[0], rows: vals.slice(1) };
  },

  write_tab: function (r) {
    var sh = SpreadsheetApp.openById(r.sheetId).getSheetByName(r.tab);
    if (!sh) throw new Error('Tab not found: ' + r.tab);
    var lock = LockService.getScriptLock(); lock.waitLock(60000);
    try {
      var edits = r.edits || [], app = r.appends || [];
      // edits: [row(1-based sheet row), col(0-based), value]
      edits.forEach(function (e) { sh.getRange(e[0], e[1] + 1).setValue(e[2]); });
      if (app.length) {
        var w = 0; app.forEach(function (x) { if (x.length > w) w = x.length; });
        var rows = app.map(function (x) { var y = x.slice(); while (y.length < w) y.push(''); return y; });
        var need = r.startRow + rows.length - 1;
        if (sh.getMaxRows() < need) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows());
        sh.getRange(r.startRow, 1, rows.length, w).setNumberFormat('@').setValues(rows);
      }
      tidy_(sh);
      SpreadsheetApp.flush();
    } finally { lock.releaseLock(); }
    return {};
  },

  read_external: function (r) {
    var m = String(r.url).match(/\/d\/([a-zA-Z0-9_-]+)/) || String(r.url).match(/[?&]id=([a-zA-Z0-9_-]+)/);
    if (!m) throw new Error('This does not look like a Google Sheet link.');
    var ss;
    try { ss = SpreadsheetApp.openById(m[1]); }
    catch (e) { throw new Error('No access to that Google Sheet. Open it with the same Google account or share it with you.'); }
    return { sheets: ss.getSheets().map(function (s) {
      var lr = s.getLastRow(), lc = s.getLastColumn();
      return { name: s.getName(), values: lr && lc ? s.getRange(1, 1, lr, lc).getDisplayValues() : [] };
    }) };
  },

  get_file: function (r) {
    var m = String(r.url).match(/\/d\/([a-zA-Z0-9_-]+)/) || String(r.url).match(/[?&]id=([a-zA-Z0-9_-]+)/) ||
            String(r.url).match(/folders\/([a-zA-Z0-9_-]+)/);
    if (!m) throw new Error('This does not look like a Google Drive file link.');
    var f;
    try { f = DriveApp.getFileById(m[1]); }
    catch (e) { throw new Error('No access to that Drive file. Share it with your Google account.'); }
    var mime = f.getMimeType(), name = f.getName(), blob;
    if (mime === MimeType.GOOGLE_SHEETS) {
      blob = f.getAs('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); name += '.xlsx';
    } else if (mime === MimeType.GOOGLE_DOCS || mime === MimeType.GOOGLE_SLIDES) {
      blob = f.getAs(MimeType.PDF); name += '.pdf';
    } else { blob = f.getBlob(); }
    if (blob.getBytes().length > 25 * 1024 * 1024) throw new Error('File is bigger than 25 MB.');
    return { name: name, mime: blob.getContentType(), b64: Utilities.base64Encode(blob.getBytes()) };
  }
};
