import copy
import json
from pathlib import Path

import pytest

from import_mitra.cod import project_records
from import_mitra.mapping import parse_source, project
from import_mitra.settings import load_config

ROOT = Path(__file__).parents[1]

@pytest.mark.parametrize('rekap', [False, True])
def test_ta_substatus_reaches_rekap_cod_and_flagging(rekap):
    config = load_config(ROOT / 'config/mitra.json')
    source = copy.deepcopy(next(s for s in config['sources'] if s['title'] == 'ta'))
    source['start'] = 2
    headers = ['SITE ID', 'SP', 'STATUS', 'SUBSTATUS', 'KETERANGAN UPDATE']
    rows = [['LBP124', 'SP7', '09. Finish Install', '08.RFS', 'remark'],
            ['EMPTY', 'SP8', '01. Survey', '', 'no status yet']]
    if rekap:
        headers = ['MITRA'] + headers
        rows = [['TA'] + r for r in rows]
    data = parse_source([headers] + rows, source, config['aliases'])
    assert project(data, ['STATUS VERIFIKASI COD', 'FLAGGING FO/RADIO'], config['aliases']) == [
        ['08.RFS', '08.RFS'], ['', '']]
    profile = json.loads((ROOT / 'config/cod2026.json').read_text())
    records, _ = project_records([data], profile['groups']['fo'], config['aliases'], 'FO')
    idx = profile['groups']['fo']['managed_columns'].index(22)
    assert [r['values'][idx] for r in records] == ['08.RFS', '']
    # The generic STATUS column must not be confused with verification status.
    assert project(data, ['STATUS'], config['aliases'])[0] == ['09. Finish Install']


def test_ta_header_drift_fails_before_writing_and_alias_is_scoped():
    config = load_config(ROOT / 'config/mitra.json')
    source = next(s for s in config['sources'] if s['title'] == 'ta')
    with pytest.raises(ValueError, match='kolom wajib hilang'):
        parse_source([['SITE ID', 'STATUS'], ['A', '08.RFS']], source, config['aliases'])
    other = {'title': 'other', 'group': 'RADIO', 'start': 2}
    data = parse_source([['SITE ID', 'SUBSTATUS'], ['A', '08.RFS']], other, config['aliases'])
    assert project(data, ['STATUS VERIFIKASI COD'], config['aliases']) == [['']]


def test_cod_formats_fix_money_percent_and_status_without_touching_values():
    from import_mitra.cod import cod_number_formats
    p = json.loads((ROOT / 'config/cod2026.json').read_text())
    requests = cod_number_formats(p['groups']['fo'], 13752)
    assert [r['repeatCell']['range']['startColumnIndex'] for r in requests] == [16, 17, 18, 21]
    assert [r['repeatCell']['cell']['userEnteredFormat']['numberFormat']['type'] for r in requests] == [
        'NUMBER', 'NUMBER', 'PERCENT', 'TEXT']
    assert all(r['repeatCell']['fields'] == 'userEnteredFormat.numberFormat' for r in requests)
    assert cod_number_formats(p['groups']['radio'], 100) == []


def test_legacy_duplicates_do_not_trigger_identity_drift_guard():
    from import_mitra.cod import keep_row_positions
    keys = [('A', 'SP', 'TA'), ('B', 'SP', 'TA'), ('C', 'SP', 'TA')]
    records = [{'key': k, 'values': [k[0]]} for k in keys]
    slots, stats = keep_row_positions(records, keys + keys + keys)
    assert stats == {'retained': 3, 'added': 0, 'removed': 6, 'rows': 3, 'last_row': 10, 'append_start': 11}
    assert slots[:3] == records
    assert slots[3:] == [None] * 6
    second, again = keep_row_positions(records, [r['key'] if r else None for r in slots])
    assert again['added'] == again['removed'] == 0
    assert second[:3] == records
    # Duplicate cleanup must not bypass drift detection for unrelated identities.
    with pytest.raises(ValueError, match='kunci lama'):
        keep_row_positions(records[:1], keys + keys)
