from import_mitra.cleaning import formula_separator, protected_formula


def test_separator_ignores_quoted_text_and_array_row_delimiters():
    assert formula_separator('=ARRAYFORMULA({"label";IF(A1=";",1,2)})') == ','
    assert formula_separator('=IF(A1=",";B1;C1)') == ';'
    assert formula_separator('=A1-B1', ';') == ';'


def test_unavailable_constants_and_api_only_ai_can_be_cleared_but_real_formulas_remain():
    assert protected_formula('=NA()') is None
    assert protected_formula('=GEMINI("classify",W2)') is None
    assert protected_formula('=SUM(', 'Formula parse error.') is None
    original = '=XLOOKUP(B2;JPP!J:J;JPP!P:P)'
    guarded = protected_formula(original)
    assert original[1:] in guarded
    assert protected_formula(guarded) == guarded
    assert 'TO_TEXT(_im_clean)' in guarded
