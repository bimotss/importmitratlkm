// Run: node --test tests/test_apps_script.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../apps_script/MitraProgress.gs'), 'utf8');
const context = vm.createContext({ Date, Utilities: {
  formatDate: (date, timezone) => new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(date)
}});
vm.runInContext(script, context);
const call = (...args) => context.mitraTransition_(...args);
const now = new Date('2026-11-18T10:00:00+07:00');
const tz = 'Asia/Jakarta';
const pairs = Object.entries(vm.runInContext('MITRA_FINISH', context));
for (const [previous, expected] of pairs) {
  test(`finishes ${previous} only on leaving it`, () => {
    assert.equal(call('', previous, {}, '', '', now, tz).stamps.length, 0);
    assert.equal(call(previous, previous, {}, '', '', now, tz).changed, false);
    assert.equal(call(previous, '10. L0 - Ready', {}, '', '', now, tz).stamps[0], expected);
    assert.equal(call(previous, '', {}, '', '', now, tz).stamps.length, 0);
  });
}
test('drop and plan drop are stamped on selection', () => {
  assert.equal(call('', '00. DROP', {}, '', '', now, tz).stamps[0], 'Tanggal DROP');
  assert.equal(call('', '00. PLAN DROP', {}, '', '', now, tz).stamps[0], 'Tanggal Plan DROP');
  assert.equal(call('', '03. L0 - Inhand NY Survey', {}, '', '', now, tz).stamps[0], 'Tanggal Inhand');
});
test('hold accumulates episodes using Jakarta calendar days', () => {
  const start = new Date('2026-11-15T23:59:00+07:00');
  const entered = call('', '00. HOLD', {}, '', 4, start, tz);
  assert.equal(entered.holdStarted, true);
  assert.equal(entered.holdDays, null);
  const exited = call('00. HOLD', '03. L0 - Survey', entered.state, start, 4, now, tz);
  assert.equal(exited.holdDays, 7);
  assert.equal(exited.state.holdStart, undefined);
  const again = call('03. L0 - Survey', '00. HOLD', exited.state, start, 7, now, tz);
  const tomorrow = new Date('2026-11-19T00:00:00+07:00');
  assert.equal(call('00. HOLD', '10. L0 - Ready', again.state, now, 7, tomorrow, tz).holdDays, 8);
});
test('hold with unknown start never invents elapsed days', () => {
  assert.equal(call('00. HOLD', '00. DROP', {}, '', '', now, tz).holdDays, null);
});
test('schema equals the importer manifest', () => {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../config/mitra.json')));
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(MITRA_HEADERS)', context)), config.partner_fo_headers);
});

function fakeSheet() {
  const headers = JSON.parse(vm.runInContext('JSON.stringify(MITRA_HEADERS)', context));
  const data = [headers, Array(headers.length).fill(''), Array(headers.length).fill('')];
  const notes = {}, formulas = {};
  const col = h => headers.indexOf(h) + 1;
  const sheet = { getName: () => 'data_TEST', getLastColumn: () => headers.length,
    getRange: (row, column, height = 1, width = 1) => ({
      getSheet: () => sheet, getRow: () => row, getColumn: () => column,
      getLastRow: () => row + height - 1, getLastColumn: () => column + width - 1,
      getNumRows: () => height, getNumColumns: () => width,
      getValues: () => data.slice(row - 1, row - 1 + height).map(r => r.slice(column - 1, column - 1 + width)),
      getValue: () => data[row - 1][column - 1],
      isBlank: () => data[row - 1][column - 1] === '',
      getFormula: () => formulas[`${row}:${column}`] || '',
      setValue(value) { data[row - 1][column - 1] = value; return this; },
      setNumberFormat() { return this; },
      getNote: () => notes[`${row}:${column}`] || '',
      setNote(value) { notes[`${row}:${column}`] = value; return this; }
    }) };
  context.LockService = { getDocumentLock: () => ({ waitLock() {}, releaseLock() {} }) };
  return { sheet, data, col, edit(row, column, oldValue, height = 1) {
    context.onEdit({ range: sheet.getRange(row, column, height), oldValue,
      source: { getSpreadsheetTimeZone: () => tz } });
  }};
}
test('onEdit fills inhand, preserves old dates, ignores unrelated edits and blank sites', () => {
  const f = fakeSheet();
  f.data[1][f.col('SITE ID') - 1] = 'A';
  f.edit(2, f.col('SITE ID'));
  assert.ok(f.data[1][f.col('Tanggal Inhand') - 1] instanceof Date);
  const historical = new Date('2026-01-01');
  f.data[1][f.col('Tanggal Finish Survey') - 1] = historical;
  f.data[1][f.col('STATUS VERIFIKASI COD') - 1] = '05. L0 - Perizinan';
  f.edit(2, f.col('STATUS VERIFIKASI COD'), '03. L0 - Survey');
  assert.equal(f.data[1][f.col('Tanggal Finish Survey') - 1], historical);
  assert.equal(f.data[1][f.col('Tanggal Finish Perizinan') - 1], '');
  f.data[2][f.col('STATUS VERIFIKASI COD') - 1] = '00. DROP';
  f.edit(3, f.col('STATUS VERIFIKASI COD'), '03. L0 - Survey');
  assert.equal(f.data[2][f.col('Tanggal DROP') - 1], '');
});
test('multirow value pastes use baseline notes to close the previous stage', () => {
  const f = fakeSheet();
  for (let row = 2; row <= 3; row++) {
    f.data[row - 1][f.col('SITE ID') - 1] = 'S' + row;
    const cell = f.sheet.getRange(row, f.col('STATUS VERIFIKASI COD'));
    cell.setNote('Catatan mitra');
    context.mitraWriteState_(cell, { progress: '03. L0 - Survey' });
    f.data[row - 1][f.col('STATUS VERIFIKASI COD') - 1] = '05. L0 - Perizinan';
  }
  f.edit(2, f.col('STATUS VERIFIKASI COD'), undefined, 2);
  for (let row = 2; row <= 3; row++) {
    assert.ok(f.data[row - 1][f.col('Tanggal Finish Survey') - 1] instanceof Date);
    assert.ok(f.sheet.getRange(row, f.col('STATUS VERIFIKASI COD')).getNote().startsWith('Catatan mitra'));
  }
});

test('duplicate delivery of a HOLD exit cannot add its duration twice', () => {
  const f = fakeSheet();
  f.data[1][f.col('SITE ID') - 1] = 'HOLD-TEST';
  f.data[1][f.col('STATUS VERIFIKASI COD') - 1] = '00. DROP';
  const start = new Date(Date.now() - 3 * 86400000);
  f.data[1][f.col('Tanggal HOLD') - 1] = start;
  f.data[1][f.col('Durasi Hold (Days)') - 1] = 2;
  context.mitraWriteState_(f.sheet.getRange(2, f.col('STATUS VERIFIKASI COD')), {
    progress: '00. HOLD', holdStart: start.toISOString()
  });
  f.edit(2, f.col('STATUS VERIFIKASI COD'), '00. HOLD');
  assert.equal(f.data[1][f.col('Durasi Hold (Days)') - 1], 5);
  f.edit(2, f.col('STATUS VERIFIKASI COD'), '00. HOLD');
  assert.equal(f.data[1][f.col('Durasi Hold (Days)') - 1], 5);
});
