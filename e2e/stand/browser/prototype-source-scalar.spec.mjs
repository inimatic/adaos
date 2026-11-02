import assert from 'node:assert/strict'
import test from 'node:test'
import { sourceScalarTextMatches } from './prototype-source-scalar.mjs'
test('canonical choice and authored enum permit capitalization of the same code',()=>{
  for(const value_type of ['choice','enum']) {
    assert(sourceScalarTextMatches({value_type},'Open','open'))
    assert(sourceScalarTextMatches({value_type},'High','high'))
    assert(!sourceScalarTextMatches({value_type},'Closed','open'))
    assert(!sourceScalarTextMatches({value_type},'Opened','open'))
  }
})
test('ordinary literal source text remains case-sensitive',()=>{
  assert(!sourceScalarTextMatches({value_type:'short_text'},'Open','open'))
  assert(sourceScalarTextMatches({value_type:'short_text'},'Replace pump','Replace pump'))
})
