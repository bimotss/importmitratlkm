/** Radio: header-based, idempotent tracking; manual edits only. */
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
const MITRA_NOTE = '\n[ImportMitra radio state] ';

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

function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() !== 'Data' || e.range.getLastRow() < 2) return;
  const cols = mitraColumns_(sheet), statusCol = cols.FLAGGINGPROGRESS || cols.PROGRESSFLAG;
  const touches = c => c && e.range.getColumn() <= c && e.range.getLastColumn() >= c;
  const idEdited = touches(cols.NEID) || touches(cols.FEID) || touches(cols.HOP);
  if (!statusCol || (!idEdited && !touches(statusCol))) return;
  const lock = LockService.getDocumentLock();
  lock.waitLock(10000);
  try {
    const first = Math.max(2, e.range.getRow()), count = e.range.getLastRow() - first + 1;
    const area = sheet.getRange(first, 1, count, sheet.getLastColumn());
    const data = area.getValues(), formulas = area.getFormulas();
    const progressRange = sheet.getRange(first, statusCol, count, 1), notes = progressRange.getNotes();
    const now = new Date(), tz = e.source.getSpreadsheetTimeZone();
    const single = e.range.getNumRows() === 1 && e.range.getNumColumns() === 1;
    const pending = new Map(), dateColumns = new Set();
    let notesChanged = false;
    const put = (row, name, value, date) => {
      const c = cols[mitraNorm_(name)];
      if (!c) return;
      if (!pending.has(c)) pending.set(c, new Map());
      pending.get(c).set(row, value);
      if (date) dateColumns.add(c);
    };
    data.forEach((values, i) => {
      const row = first + i;
      const val = key => cols[key] ? values[cols[key] - 1] : '';
      if (!String(val('NEID') || val('FEID') || val('HOP')).trim()) return;
      const stamp = name => {
        const c = cols[mitraNorm_(name)];
        if (c && values[c - 1] === '' && !formulas[i][c - 1]) put(row, name, now, true);
      };
      if (idEdited) { stamp('Tanggal Inhand'); stamp('Inhand Date'); }
      if (!touches(statusCol)) return;
      const current = String(values[statusCol - 1] || '').trim();
      const state = mitraReadState_(notes[i][0]);
      if (state && state.progress === current) return;
      const previous = single && e.oldValue !== undefined ? String(e.oldValue).trim() : (state || {}).progress || '';
      const result = mitraTransition_(previous, current, state, val('TANGGALHOLD'), val('DURASIHOLDDAYS'), now, tz);
      if (!result.changed) return;
      result.stamps.forEach(stamp);
      if (result.holdStarted) put(row, 'Tanggal HOLD', now, true);
      if (result.holdDays !== null) put(row, 'Durasi Hold (Days)', result.holdDays, false);
      put(row, 'Tanggal Ganti Flagging', now, true);
      // Keep the existing order/cutoff field intact; LAST UPDATE STATUS tracks progress.
      put(row, 'LAST UPDATE STATUS', current, false);
      const age = cols.AGINGPROGRESS, group = cols.GROUPINGAGINGPROGRESS;
      if (age && !formulas[i][age - 1]) {
        put(row, 'Aging Progress', '=IF(' + radioCol_(cols.TANGGALGANTIFLAGGING) + row + '<>"",MAX(0,TODAY()-INT(' + radioCol_(cols.TANGGALGANTIFLAGGING) + row + ')),"")', false);
      }
      if (group && !formulas[i][group - 1]) {
        put(row, 'Grouping Aging Progress', '=IF(' + radioCol_(age) + row + '<>"",IF(' + radioCol_(age) + row + '>30,"> 30 Days","\u2264 30 Days"),"")', false);
      }
      const note = notes[i][0], pos = note.lastIndexOf(MITRA_NOTE);
      notes[i][0] = (pos < 0 ? note : note.slice(0, pos)) + MITRA_NOTE + JSON.stringify(result.state);
      notesChanged = true;
    });
    // Batch contiguous cells per column, avoiding one API write per pasted row.
    pending.forEach((rows, column) => {
      const entries = [...rows.entries()].sort((a, b) => a[0] - b[0]);
      let start = 0;
      while (start < entries.length) {
        let end = start + 1;
        while (end < entries.length && entries[end][0] === entries[end - 1][0] + 1) end++;
        const target = sheet.getRange(entries[start][0], column, end - start, 1);
        if (dateColumns.has(column)) target.setNumberFormat('dd/MM/yyyy');
        else if (column === cols.DURASIHOLDDAYS || column === cols.AGINGPROGRESS) target.setNumberFormat('0');
        target.setValues(entries.slice(start, end).map(x => [x[1]]));
        start = end;
      }
    });
    if (notesChanged) progressRange.setNotes(notes);
  } finally { lock.releaseLock(); }
}
function radioCol_(index) {
  if (!index) throw new Error('Header tanggal ganti flagging/aging belum tersedia');
  let text = '';
  while (index) { index--; text = String.fromCharCode(65 + index % 26) + text; index = Math.floor(index / 26); }
  return text;
}
