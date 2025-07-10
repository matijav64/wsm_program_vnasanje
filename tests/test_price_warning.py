from decimal import Decimal
from wsm.ui.review.gui import _apply_price_warning

class DummyTree:
    def __init__(self):
        self.tags = {}
    def item(self, iid, **kw):
        if kw:
            if 'tags' in kw:
                self.tags[iid] = kw['tags']
        return {'tags': self.tags.get(iid)}

def test_apply_price_warning_none():
    tree = DummyTree()
    tooltip = _apply_price_warning(tree, '1', Decimal('1'), None)
    assert tree.tags.get('1') == ()
    assert tooltip is None

def test_apply_price_warning_within_threshold():
    tree = DummyTree()
    tooltip = _apply_price_warning(tree, '1', Decimal('10.01'), Decimal('10'), threshold=Decimal('5'))
    assert tree.tags.get('1') == ()
    assert tooltip == ""

def test_apply_price_warning_exceeds_threshold():
    tree = DummyTree()
    tooltip = _apply_price_warning(tree, '1', Decimal('11'), Decimal('10'), threshold=Decimal('5'))
    assert tree.tags.get('1') == ('price_warn',)
    assert tooltip == "±1.00 €"
