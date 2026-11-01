// Consume the Core-owned bounded grammar, never a second caption allowlist.
export function classifyTimestampCaption(label, grammar) {
 const text=String(label).replace(/\s+/gu,' ').trim().replace(/[:?]+$/u,'').trim().toLowerCase()
 const cues=Object.entries(grammar.cues).filter(([,pattern])=>new RegExp(pattern,'u').test(text))
 if(cues.length>1||new RegExp(grammar.operators,'u').test(text))
  return {status:'unresolved',reason:'timestamp_caption_polarity_or_alternatives',grammar_ref:grammar.grammar_ref}
 const matches=Object.entries(grammar.patterns).filter(([,patterns])=>patterns.some(pattern=>new RegExp(`^(?:${pattern})$`,'u').test(text)))
 return matches.length===1?{status:'recognized',meaning:matches[0][0],grammar_ref:grammar.grammar_ref}
  :{status:'unresolved',reason:'timestamp_caption_outside_grammar',grammar_ref:grammar.grammar_ref}
}
