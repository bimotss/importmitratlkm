/** Pasang pada Extensions > Apps Script di setiap spreadsheet mitra. */
const MITRA_HEADERS = [
  "NO",
  "UNIQUE",
  "SITE ID",
  "SITE NAME",
  "SP",
  "REGIONAL TIF",
  "WITEL",
  "Grouping",
  "STO",
  "DASAR ORDER",
  "STATUS INFRA",
  "TAHUN PROJECT",
  "PROJECT OWNER",
  "CNOP / OLO",
  "TEMATIK",
  "PRIORITAS ORDER",
  "NILAI DRM/WO",
  "NILAI REKON/REVIEW",
  "%Deviasi",
  "APPROVAL COD & DID",
  "Tgl APPROVAL GO",
  "STATUS VERIFIKASI COD",
  "KETERANGAN UPDATE",
  "STATUS OA",
  "Tgl Eval",
  "Keterangan Eval",
  "NILAI APPROVAL DESIGN",
  "GROUPING KENDALA",
  "GROUPING PERIZINAN",
  "GROUPING ALASAN DROP",
  "NILAI PERIZINAN",
  "TGL BA PERIZINAN",
  "KETERANGAN BA PERIZINAN",
  "STATUS BA PERIZINAN",
  "SUBKON",
  "PLAN RFS",
  "Tanggal Inhand",
  "Tanggal Finish Survey",
  "Tanggal Finish DRM",
  "Tanggal Finish Perizinan",
  "Tanggal Finish Material Preparation",
  "Tanggal Material Onsite",
  "Tanggal Finish Instalasi",
  "Tanggal Finish CT",
  "Tanggal Finish UT",
  "Tanggal Finish BAST",
  "Tanggal DROP",
  "Tanggal Plan DROP",
  "Tanggal HOLD",
  "Durasi Hold (Days)",
  "Jarak WO",
  "Catuan WO",
  "Progress Cut Off",
  "Real Start Progress",
  "Aging Progress",
  "Grouping Aging Progress",
  "NIM order"
];
const MITRA_PROGRESS = [
  '00. DROP', '00. PLAN DROP', '00. HOLD', '03. L0 - Inhand NY Survey',
  '03. L0 - Survey', '04. L0 - DRM - NY Approve', '04. L0 - DRM - Approve',
  '05. L0 - Perizinan', '06. L0 - Material Preparation', '07. L0 - Material Delivery',
  '08. L0 - Progress Instalasi', '09. L0 - Ready Terkendala', '10. L0 - Ready',
  '10. L0 - Ready CT', '10. L0 - Ready UT', '10. L0 - Ready BAST', '14. Done BAST'
];
const MITRA_FINISH = {
  '03. L0 - Survey': 'Tanggal Finish Survey',
  '04. L0 - DRM - Approve': 'Tanggal Finish DRM',
  '05. L0 - Perizinan': 'Tanggal Finish Perizinan',
  '06. L0 - Material Preparation': 'Tanggal Finish Material Preparation',
  '07. L0 - Material Delivery': 'Tanggal Material Onsite',
  '08. L0 - Progress Instalasi': 'Tanggal Finish Instalasi',
  '10. L0 - Ready CT': 'Tanggal Finish CT',
  '10. L0 - Ready UT': 'Tanggal Finish UT',
  '10. L0 - Ready BAST': 'Tanggal Finish BAST'
};
const MITRA_NOTE = '\n[ImportMitra state] ';

function mitraNorm_(v) { return String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, ''); }
function mitraColumns_(sheet) {
  const cols = {};
  sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].forEach((h, i) => {
    cols[mitraNorm_(h)] = i + 1;
  });
  return cols;
}
function mitraReadState_(note) {
  const pos = note.lastIndexOf(MITRA_NOTE);
  if (pos < 0) return null;
  try { return JSON.parse(note.slice(pos + MITRA_NOTE.length)); } catch (_) { return null; }
}
function mitraWriteState_(cell, state) {
  const note = cell.getNote();
  const pos = note.lastIndexOf(MITRA_NOTE);
  cell.setNote((pos < 0 ? note : note.slice(0, pos)) + MITRA_NOTE + JSON.stringify(state));
}
function mitraDay_(value, timezone) {
  if (!(value instanceof Date) || isNaN(value.getTime())) return null;
  const p = Utilities.formatDate(value, timezone, 'yyyy-MM-dd').split('-').map(Number);
  return Date.UTC(p[0], p[1] - 1, p[2]) / 86400000;
}

/** Pure transition function; dates are event dates, never inferred from stage order. */
function mitraTransition_(previous, current, state, holdDate, duration, now, timezone) {
  const next = Object.assign({}, state || {});
  const result = { state: next, stamps: [], holdDays: null, holdStarted: false, changed: false };
  if (!current || current === previous || MITRA_PROGRESS.indexOf(current) < 0) return result;
  result.changed = true;
  if (MITRA_FINISH[previous]) result.stamps.push(MITRA_FINISH[previous]);
  if (previous === '00. HOLD') {
    const start = next.holdStart ? new Date(next.holdStart) : holdDate;
    const startDay = mitraDay_(start, timezone), endDay = mitraDay_(now, timezone);
    if (startDay !== null) {
      result.holdDays = Math.max(0, Number(duration) || 0) + Math.max(0, endDay - startDay);
    }
    delete next.holdStart;
  }
  const enter = { '00. DROP': 'Tanggal DROP', '00. PLAN DROP': 'Tanggal Plan DROP',
    '03. L0 - Inhand NY Survey': 'Tanggal Inhand' };
  if (enter[current]) result.stamps.push(enter[current]);
  if (current === '00. HOLD') {
    next.holdStart = now.toISOString();
    result.holdStarted = true;
  }
  next.progress = current;
  return result;
}

/** Simple trigger: edit manual saja; jangan pasang trigger tambahan untuk fungsi ini. */
function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (!/^data_/i.test(sheet.getName()) || e.range.getLastRow() < 2) return;
  const cols = mitraColumns_(sheet);
  const siteCol = cols.SITEID, progressCol = cols.STATUSVERIFIKASICOD;
  if (!siteCol || !progressCol) return;
  const touches = c => e.range.getColumn() <= c && e.range.getLastColumn() >= c;
  const singleCell = e.range.getNumRows() === 1 && e.range.getNumColumns() === 1;
  const tracking = ['PROGRESSCUTOFF', 'REALSTARTPROGRESS', 'AGINGPROGRESS', 'GROUPINGAGINGPROGRESS'];
  if (singleCell && tracking.some(key => cols[key] && touches(cols[key]))) {
    const email = (Session.getActiveUser().getEmail() || (e.user ? e.user.getEmail() : '')).toLowerCase();
    if (email && email !== 'bimotafirst@gmail.com') {
      e.range.setValue(e.oldValue !== undefined ? e.oldValue : '');
      tracking.forEach(key => { if (cols[key]) sheet.hideColumns(cols[key], 1); });
    }
    return;
  }
  if (!touches(siteCol) && !touches(progressCol)) return;
  const lock = LockService.getDocumentLock();
  lock.waitLock(10000);
  try {
    const now = new Date(), tz = e.source.getSpreadsheetTimeZone();
    for (let row = Math.max(2, e.range.getRow()); row <= e.range.getLastRow(); row++) {
      if (!String(sheet.getRange(row, siteCol).getValue()).trim()) continue;
      const target = name => {
        const col = cols[mitraNorm_(name)];
        if (!col) throw new Error('Kolom belum tersedia: ' + name + '. Jalankan setupMitra().');
        return sheet.getRange(row, col);
      };
      const stamp = name => {
        const cell = target(name);
        if (cell.isBlank() && !cell.getFormula()) cell.setValue(now).setNumberFormat('dd/MM/yyyy');
      };
      if (touches(siteCol)) stamp('Tanggal Inhand');
      if (!touches(progressCol)) continue;
      const cell = sheet.getRange(row, progressCol);
      const current = String(cell.getValue() || '').trim();
      const state = mitraReadState_(cell.getNote());
      if (state && state.progress === current) continue;
      const single = e.range.getNumRows() === 1 && e.range.getNumColumns() === 1;
      const previous = single && e.oldValue !== undefined ? String(e.oldValue).trim() : (state || {}).progress || '';
      const result = mitraTransition_(previous, current, state, target('Tanggal HOLD').getValue(),
        target('Durasi Hold (Days)').getValue(), now, tz);
      if (!result.changed) continue;
      result.stamps.forEach(stamp);
      if (result.holdStarted) target('Tanggal HOLD').setValue(now).setNumberFormat('dd/MM/yyyy');
      if (result.holdDays !== null) target('Durasi Hold (Days)').setValue(result.holdDays).setNumberFormat('0');
      target('Real Start Progress').setValue(now).setNumberFormat('dd/MM/yyyy');
      target('Progress Cut Off').setValue(current);
      target('Aging Progress').setValue(0);
      target('Grouping Aging Progress').setNumberFormat('@').setValue('1-10');
      mitraWriteState_(cell, result.state);
    }
  } finally { lock.releaseLock(); }
}

/** Jalankan sekali di tiap file. Kolom lama di luar skema disimpan di kanan. */
function setupMitra() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.setSpreadsheetTimeZone('Asia/Jakarta');
  book.getSheets().filter(s => /^data_/i.test(s.getName())).forEach(sheet => {
    const original = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    if (!original.some(h => mitraNorm_(h) === 'SITEID')) throw new Error('SITE ID tidak ditemukan');
    MITRA_HEADERS.forEach((header, i) => {
      const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
      let index = headers.findIndex(h => mitraNorm_(h) === mitraNorm_(header));
      if (index < 0 && header === 'Tanggal Inhand') {
        index = headers.findIndex(h => ['TGLORDERDITERIMA', 'TGLINHAND'].includes(mitraNorm_(h)));
      }
      if (index < 0 && header === 'NO' && i === 0 && headers[0] === 'x`') index = 0;
      if (index < 0) sheet.insertColumnBefore(i + 1);
      else if (index !== i) sheet.moveColumns(sheet.getRange(1, index + 1, sheet.getMaxRows(), 1), i + 1);
      sheet.getRange(1, i + 1).setValue(header);
    });
    sheet.setFrozenRows(1);
    const cols = mitraColumns_(sheet), count = sheet.getMaxRows() - 1;
    const progress = sheet.getRange(2, cols.STATUSVERIFIKASICOD, count, 1);
    progress.setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(MITRA_PROGRESS, true)
      .setAllowInvalid(false).build());
    MITRA_HEADERS.filter(h => /^Tanggal |^TGL |^Tgl |^PLAN RFS$|^Real Start Progress$/.test(h)).forEach(h => {
      sheet.getRange(2, cols[mitraNorm_(h)], count, 1).setNumberFormat('dd/MM/yyyy');
    });
    // Baseline existing progress for multirow pastes; do not invent historic dates.
    const used = Math.max(0, sheet.getLastRow() - 1);
    if (used) {
      const range = sheet.getRange(2, cols.STATUSVERIFIKASICOD, used, 1);
      const values = range.getValues(), notes = range.getNotes();
      values.forEach((r, i) => {
        if (!mitraReadState_(notes[i][0]) && r[0]) {
          notes[i][0] += MITRA_NOTE + JSON.stringify({ progress: String(r[0]).trim() });
        }
      });
      range.setNotes(notes);
    }
  });
}

/** Keep the existing daily trigger name, resolving columns by header. */
function updateDailyAging() {
  const book = SpreadsheetApp.getActiveSpreadsheet(), tz = book.getSpreadsheetTimeZone();
  const today = mitraDay_(new Date(), tz);
  book.getSheets().filter(s => /^data_/i.test(s.getName())).forEach(sheet => {
    const cols = mitraColumns_(sheet), count = sheet.getLastRow() - 1;
    if (count < 1 || !cols.REALSTARTPROGRESS || !cols.AGINGPROGRESS || !cols.GROUPINGAGINGPROGRESS) return;
    const values = sheet.getRange(2, cols.REALSTARTPROGRESS, count, 1).getValues();
    const aging = [], groups = [];
    values.forEach(row => {
      let value = row[0];
      if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        value = new Date(value + 'T00:00:00+07:00');
      }
      const start = mitraDay_(value, tz);
      const days = start === null ? '' : Math.max(0, today - start);
      aging.push([days]);
      groups.push([days === '' ? '' : days <= 10 ? '1-10' : days <= 20 ? '11-20' : days <= 30 ? '21-30' : '>30']);
    });
    sheet.getRange(2, cols.AGINGPROGRESS, count, 1).setValues(aging);
    sheet.getRange(2, cols.GROUPINGAGINGPROGRESS, count, 1).setNumberFormat('@').setValues(groups);
  });
}
