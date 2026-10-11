import test from 'node:test'
import assert from 'node:assert/strict'
import { selectionPresentationExpectation } from './prototype-relationship-links.mjs'

const records = [{id:'a',parent:0,scope:'x'},{id:'b',parent:1,scope:'x'},{id:'c',parent:0,scope:'y'}]
const view = effect => ({selection_filter:{field_ref:'parent',effect},scope_filters:[{field_ref:'scope',value:'x'}]})
test('filter presents only matching scoped records, including numeric zero',()=>{
  assert.deepEqual(selectionPresentationExpectation(view('filter'),records,0),{effect:'filter',related:['a'],records:['a']})
})
test('emphasize preserves scoped rows and marks only the related rows',()=>{
  assert.deepEqual(selectionPresentationExpectation(view('emphasize'),records,0),{effect:'emphasize',related:['a'],records:['a','b']})
})
test('emphasis with no matching rows preserves rows but highlights none',()=>{
  assert.deepEqual(selectionPresentationExpectation(view('emphasize'),records,9),{effect:'emphasize',related:[],records:['a','b']})
})
test('missing effect retains the canonical filter default',()=>{
  assert.deepEqual(selectionPresentationExpectation(view(undefined),records,1).records,['b'])
})
test('unknown effects fail instead of assuming equivalent semantics',()=>{
  assert.throws(()=>selectionPresentationExpectation(view('invented'),records,0))
})
test('undeclared field does not silently qualify',()=>{
  assert.throws(()=>selectionPresentationExpectation({selection_filter:{effect:'emphasize'}},records,0))
})
