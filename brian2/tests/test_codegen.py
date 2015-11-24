from collections import namedtuple

import numpy as np
from nose.plugins.attrib import attr

from brian2.codegen.optimisation import optimise_statements
from brian2.codegen.translation import (analyse_identifiers,
                                        get_identifiers_recursively,
                                        parse_statement,
                                        make_statements,
                                        )
from brian2.codegen.statements import Statement
from brian2.codegen.codeobject import CodeObject
from brian2.parsing.sympytools import str_to_sympy, sympy_to_str
from brian2.core.variables import Subexpression, Variable, Constant, ArrayVariable
from brian2.core.functions import Function, DEFAULT_FUNCTIONS, DEFAULT_CONSTANTS
from brian2.devices.device import auto_target, device
from brian2.units.fundamentalunits import Unit
from brian2.units import second, ms

FakeGroup = namedtuple('FakeGroup', ['variables'])

@attr('codegen-independent')
def test_auto_target():
    # very basic test that the "auto" codegen target is useable
    assert issubclass(auto_target(), CodeObject)


@attr('codegen-independent')
def test_analyse_identifiers():
    '''
    Test that the analyse_identifiers function works on a simple clear example.
    '''
    code = '''
    a = b+c
    d = e+f
    '''
    known = {'b': Variable(unit=None, name='b'),
             'c': Variable(unit=None, name='c'),
             'd': Variable(unit=None, name='d'),
             'g': Variable(unit=None, name='g')}
    
    defined, used_known, dependent = analyse_identifiers(code, known)
    assert 'a' in defined  # There might be an additional constant added by the
                           # loop-invariant optimisation
    assert used_known == {'b', 'c', 'd'}
    assert dependent == {'e', 'f'}


@attr('codegen-independent')
def test_get_identifiers_recursively():
    '''
    Test finding identifiers including subexpressions.
    '''
    variables = {'sub1': Subexpression(name='sub1', unit=Unit(1),
                                       dtype=np.float32, expr='sub2 * z',
                                       owner=FakeGroup(variables={}),
                                       device=None),
                 'sub2': Subexpression(name='sub2', unit=Unit(1),
                                       dtype=np.float32, expr='5 + y',
                                       owner=FakeGroup(variables={}),
                                       device=None),
                 'x': Variable(unit=None, name='x')}
    identifiers = get_identifiers_recursively(['_x = sub1 + x'],
                                              variables)
    assert identifiers == {'x', '_x', 'y', 'z', 'sub1', 'sub2'}


@attr('codegen-independent')
def test_nested_subexpressions():
    '''
    This test checks that code translation works with nested subexpressions.
    '''
    code = '''
    x = a + b + c
    c = 1
    x = a + b + c
    d = 1
    x = a + b + c
    '''
    variables = {
        'a': Subexpression(name='a', unit=Unit(1), dtype=np.float32, owner=FakeGroup(variables={}), device=None,
                           expr='b*b+d'),
        'b': Subexpression(name='b', unit=Unit(1), dtype=np.float32, owner=FakeGroup(variables={}), device=None,
                           expr='c*c*c'),
        'c': Variable(unit=None, name='c'),
        'd': Variable(unit=None, name='d'),
        }
    scalar_stmts, vector_stmts = make_statements(code, variables, np.float32)
    assert len(scalar_stmts) == 0
    evalorder = ''.join(stmt.var for stmt in vector_stmts)
    # This is the order that variables ought to be evaluated in
    assert evalorder=='baxcbaxdax'

@attr('codegen-independent')
def test_apply_loop_invariant_optimisation():
    variables = {'v': Variable('v', Unit(1), scalar=False),
                 'w': Variable('w', Unit(1), scalar=False),
                 'dt': Constant('dt', second, 0.1*ms),
                 'tau': Constant('tau', second, 10*ms),
                 'exp': DEFAULT_FUNCTIONS['exp']}
    statements = [Statement('v', '=', 'dt*w*exp(-dt/tau)/tau + v*exp(-dt/tau)', '', np.float32),
                  Statement('w', '=', 'w*exp(-dt/tau)', '', np.float32)]
    scalar, vector = optimise_statements([], statements, variables)
    # The optimisation should pull out at least exp(-dt / tau)
    assert len(scalar) >= 1
    assert np.issubdtype(scalar[0].dtype, (np.floating, float))
    assert scalar[0].var == '_lio_1'
    assert len(vector) == 2
    assert all('_lio_' in stmt.expr for stmt in vector)

@attr('codegen-independent')
def test_apply_loop_invariant_optimisation_integer():
    variables = {'v': Variable('v', Unit(1), scalar=False),
                 'N': Constant('N', Unit(1), 10),
                 'b': Variable('b', Unit(1), scalar=True, dtype=int),
                 'c': Variable('c', Unit(1), scalar=True, dtype=int),
                 'd': Variable('d', Unit(1), scalar=True, dtype=int),
                 'y': Variable('y', Unit(1), scalar=True, dtype=float),
                 'z': Variable('z', Unit(1), scalar=True, dtype=float),
                 'w': Variable('w', Unit(1), scalar=True, dtype=float),
                 }
    statements = [Statement('v', '=', 'v % (2*3*N)', '', np.float32),
                  # integer version doesn't get rewritten but float version does
                  Statement('a', ':=', 'b/(c/d)', '', int),
                  Statement('x', ':=', 'y/(z/w)', '', float),
                  ]
    scalar, vector = optimise_statements([], statements, variables)
    assert len(scalar) == 3
    assert np.issubdtype(scalar[0].dtype, (np.integer, int))
    assert scalar[0].var == '_lio_1'
    expr = scalar[0].expr.replace(' ', '')
    assert expr=='6*N' or expr=='N*6'
    assert np.issubdtype(scalar[1].dtype, (np.integer, int))
    assert scalar[1].var == '_lio_2'
    expr = scalar[1].expr.replace(' ', '')
    assert expr=='b/(c/d)'
    assert np.issubdtype(scalar[2].dtype, (np.float, float))
    assert scalar[2].var == '_lio_3'
    expr = scalar[2].expr.replace(' ', '')
    assert expr=='(y*w)/z' or expr=='(w*y)/z'

@attr('codegen-independent')
def test_apply_loop_invariant_optimisation_no_optimisation():
    variables = {'v1': Variable('v1', Unit(1), scalar=False),
                 'v2': Variable('v2', Unit(1), scalar=False),
                 'N': Constant('N', Unit(1), 10),
                 's1': Variable('s1', Unit(1), scalar=True, dtype=float),
                 's2': Variable('s2', Unit(1), scalar=True, dtype=float),
                 'rand': DEFAULT_FUNCTIONS['rand']
                 }
    statements = [
        # Neither should this
        Statement('v1', '=', 'rand() - rand()', '', np.float),
        Statement('v1', '=', '3*rand() - 3*rand()', '', np.float),
        Statement('v1', '=', '3*rand() - ((1+2)*rand())', '', np.float),
        # This should not pull out rand()*N
        Statement('v1', '=', 's1*rand()*N', '', np.float),
        Statement('v1', '=', 's2*rand()*N', '', np.float)
    ]
    scalar, vector = optimise_statements([], statements, variables)
    for vs in vector:
        assert 'rand()' in vs.expr, 'Expression should still contain rand(), but got ' + str(vs)

@attr('codegen-independent')
def test_automatic_augmented_assignments():
    # We test that statements that could be rewritten as augmented assignments
    # are correctly rewritten (using sympy to test for symbolic equality)
    variables = {
        'x': ArrayVariable('x', unit=Unit(1), owner=None, size=10,
                           device=device),
        'y': ArrayVariable('y', unit=Unit(1), owner=None, size=10,
                           device=device),
        'z': ArrayVariable('y', unit=Unit(1), owner=None, size=10,
                           device=device),
        'b': ArrayVariable('b', unit=Unit(1), owner=None, size=10,
                           dtype=np.bool, device=device),
        'clip': DEFAULT_FUNCTIONS['clip'],
        'inf': DEFAULT_CONSTANTS['inf']
    }
    statements = [
        # examples that should be rewritten
        # Note that using our approach, we will never get -= or /= but always
        # the equivalent += or *= statements
        ('x = x + 1', 'x += 1'),
        ('x = 2 * x', 'x *= 2'),
        ('x = x - 3', 'x += -3'),
        ('x = x/2', 'x *= 0.5'),
        ('x = y + (x + 1)', 'x += y + 1'),
        ('x = x + x', 'x *= 2'),
        ('x = x + y + z', 'x += y + z'),
        ('x = x + y + z', 'x += y + z'),
        # examples that should not be rewritten
        ('x = 1/x', 'x = 1/x'),
        ('x = 1', 'x = 1'),
        ('x = 2*(x + 1)', 'x = 2*(x + 1)'),
        ('x = clip(x + y, 0, inf)', 'x = clip(x + y, 0, inf)'),
        ('b = b or False', 'b = b or False')
    ]
    for orig, rewritten in statements:
        scalar, vector = make_statements(orig, variables, np.float32)
        try:  # we augment the assertion error with the original statement
            assert len(scalar) == 0, 'Did not expect any scalar statements but got ' + str(scalar)
            assert len(vector) == 1, 'Did expect a single statement but got ' + str(vector)
            statement = vector[0]
            expected_var, expected_op, expected_expr, _ = parse_statement(rewritten)
            assert expected_var == statement.var, 'expected write to variable %s, not to %s' % (expected_var, statement.var)
            assert expected_op == statement.op, 'expected operation %s, not %s' % (expected_op, statement.op)
            # Compare the two expressions using sympy to allow for different order etc.
            sympy_expected = str_to_sympy(expected_expr)
            sympy_actual = str_to_sympy(statement.expr)
            assert sympy_expected == sympy_actual, ('RHS expressions "%s" and "%s" are not identical' % (sympy_to_str(sympy_expected),
                                                                                                         sympy_to_str(sympy_actual)))
        except AssertionError as ex:
            raise AssertionError('Transformation for statement "%s" gave an unexpected result: %s' % (orig, str(ex)))


if __name__ == '__main__':
    test_auto_target()
    test_analyse_identifiers()
    test_get_identifiers_recursively()
    test_nested_subexpressions()
    test_apply_loop_invariant_optimisation()
    test_apply_loop_invariant_optimisation_integer()
    test_apply_loop_invariant_optimisation_no_optimisation()
    test_automatic_augmented_assignments()
