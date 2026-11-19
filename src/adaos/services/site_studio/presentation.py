"""Bounded presentation contracts shared by authoring and the public renderer.

Artwork uses SVG names and explicit SMIL timing. It carries no HTML, CSS,
selectors, event handlers, network references or executable expressions.
Brand geometry belongs to resource data, never to a renderer branch.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy

TAGS = ('g', 'path', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'rect', 'text', 'animate', 'animateTransform')
ATTRIBUTES = ('id', 'd', 'points', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height',
              'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-dashoffset',
              'opacity', 'fill-opacity', 'stroke-opacity', 'transform', 'font-family', 'font-size', 'font-weight', 'letter-spacing',
              'text-anchor', 'attributeName', 'from', 'to', 'values', 'begin', 'dur', 'repeatCount', 'type',
              'calcMode', 'keyTimes', 'keySplines', 'additive', 'easing')
ANIMATED = ('stroke-dashoffset', 'opacity', 'fill-opacity', 'stroke-opacity', 'transform', 'cx', 'cy', 'r', 'x', 'y')


def presentation_properties(component_id):
    result = {'headingHidden': {'type': 'boolean'}, 'presentation': {'type': 'object', 'additionalProperties': False, 'properties': {
        'style': {'enum': ['standard', 'editorial']},
        'reveal': {'enum': ['none', 'fade', 'stagger']},
    }}}
    if component_id in {'site.section', 'site.cardGrid'}:
        result['presentation']['properties'].update(
            paddingTop={'type': 'integer', 'minimum': 0, 'maximum': 240},
            paddingBottom={'type': 'integer', 'minimum': 0, 'maximum': 240})
    if component_id == 'site.section':
        result['presentation']['properties']['height'] = {'enum': ['content', 'viewport']}
    if component_id == 'site.header':
        result.update(variant={'enum': ['default', 'drawer']}, menuLabel={'type': 'string', 'maxLength': 80},
                      closeLabel={'type': 'string', 'maxLength': 80})
        result['presentation']['properties'].update(scrollProgress={'type': 'boolean'}, scrollSnap={'type': 'boolean'})
    if component_id in {'site.hero', 'site.divider'}:
        result.update(artwork=artwork_schema())
    if component_id == 'site.hero':
        result.update(scrollCue={'type': 'boolean'})
    if component_id == 'site.section':
        result.update(titleAccent={'type': 'string', 'maxLength': 240}, emphasis={
            'type': 'array', 'maxItems': 16, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 240}})
        result.update(lockup={'type': 'object', 'oneOf': [{'required': ['src']}, {'required': ['artwork']}],
            'additionalProperties': False, 'properties': {'src': {'type': 'string', 'maxLength': 512},
                'alt': {'type': 'string', 'maxLength': 240}, 'artwork': artwork_schema()}})
    if component_id == 'site.cardGrid':
        result['presentation']['properties']['noteAlign'] = {'enum': ['left', 'center', 'right']}
        result.update(columns={'anyOf': [{'type': 'integer', 'minimum': 1, 'maximum': 4},
                                        {'type': 'array', 'maxItems': 8}]})
        result.update(portrait={'type': 'object', 'additionalProperties': False, 'properties': {
            'shape': {'enum': ['rectangle', 'circle']},
        }})
    if component_id == 'site.footer':
        result.update(media={'type': 'object', 'required': ['src'], 'properties': {
            'src': {'type': 'string', 'maxLength': 512}, 'alt': {'type': 'string', 'maxLength': 240}}})
    return result


def artwork_schema():
    return {'type': 'object', 'x-site-editor': 'chat', 'required': ['viewBox', 'label', 'nodes'], 'additionalProperties': False, 'properties': {
        'viewBox': {'type': 'string', 'maxLength': 100}, 'label': {'type': 'string', 'minLength': 1, 'maxLength': 240},
        'nodes': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {'$ref': '#/$defs/artworkNode'}},
    }}


def artwork_definitions():
    return {'artworkNode': {'type': 'object', 'required': ['tag', 'attrs'], 'additionalProperties': False, 'properties': {
        'tag': {'enum': list(TAGS)},
        'attrs': {'type': 'object', 'maxProperties': 32, 'propertyNames': {'enum': list(ATTRIBUTES)},
                  'additionalProperties': {'type': ['string', 'number']}},
        'text': {'type': 'string', 'maxLength': 500},
        'children': {'type': 'array', 'maxItems': 128, 'items': {'$ref': '#/$defs/artworkNode'}},
    }}}


def validate_artwork(artwork):
    if artwork is None:
        return
    try:
        viewbox = [float(v) for v in re.split(r'[\s,]+', artwork['viewBox'].strip())]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('artwork requires a finite SVG viewBox') from exc
    if len(viewbox) != 4 or not all(math.isfinite(v) for v in viewbox) or not all(0 < v <= 8192 for v in viewbox[2:]):
        raise ValueError('artwork viewBox exceeds supported dimensions')
    count = 0

    def visit(node, depth=0):
        nonlocal count
        count += 1
        if count > 128 or depth > 8:
            raise ValueError('artwork exceeds the bounded geometry budget')
        if node['tag'] not in TAGS or set(node['attrs']) - set(ATTRIBUTES):
            raise ValueError('artwork contains an unsupported SVG element or attribute')
        for key, value in node['attrs'].items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError('artwork values must be finite')
            if len(str(value)) > 4096 or re.search(r'[<>]|url\s*\(|javascript:|data:|https?:|[\\@]', str(value), re.I):
                raise ValueError('artwork cannot contain external references or executable values')
            if key in {'fill', 'stroke'} and node['tag'] not in {'animate', 'animateTransform'}:
                if not re.fullmatch(r'(?:none|currentColor|accent|ink|muted|#[a-fA-F0-9]{3,8})', str(value)):
                    raise ValueError('artwork paint must be a color or a named theme role')
            if key == 'transform' and not re.fullmatch(r'(?:\s*(?:translate|scale|rotate|matrix|skewX|skewY)\([\d.,+eE\s-]+\)\s*)+', str(value)):
                raise ValueError('artwork transform must contain only numeric SVG transforms')
        if node['tag'] in {'animate', 'animateTransform'}:
            attrs = node['attrs']
            if 'easing' in attrs:
                match = re.fullmatch(r'cubic-bezier\(([^()]*)\)', str(attrs['easing']))
                try:
                    curve = [float(value.strip()) for value in match[1].split(',')] if match else []
                    start = [float(value) for value in re.split(r'[\s,]+', str(attrs.get('from', '')).strip())]
                    end = [float(value) for value in re.split(r'[\s,]+', str(attrs.get('to', '')).strip())]
                except (ValueError, TypeError) as exc:
                    raise ValueError('artwork easing requires a numeric curve and from/to values') from exc
                if (len(curve) != 4 or not all(math.isfinite(value) for value in curve)
                    or not 0 <= curve[0] <= 1 or not 0 <= curve[2] <= 1
                    or abs(curve[1]) > 4 or abs(curve[3]) > 4
                    or not 1 <= len(start) == len(end) <= 3
                    or not all(math.isfinite(value) and abs(value) <= 1000000 for value in start + end)
                    or 'values' in attrs):
                    raise ValueError('artwork easing requires bounded cubic-bezier and numeric from/to values')
            if attrs.get('attributeName') not in ANIMATED:
                raise ValueError('artwork animation target is unsupported')
            if node['tag'] == 'animateTransform' and (attrs.get('attributeName') != 'transform' or attrs.get('type') not in {'translate', 'scale', 'rotate'}):
                raise ValueError('artwork animation transform is unsupported')
            for key in ('begin', 'dur'):
                if key in attrs and not re.fullmatch(r'\d+(?:\.\d+)?(?:ms|s)', str(attrs[key])):
                    raise ValueError('artwork animation timing must be an explicit time')
            if attrs.get('fill', 'freeze') not in {'freeze', 'remove'}:
                raise ValueError('artwork animation fill is unsupported')
            if 'repeatCount' in attrs and not re.fullmatch(r'(?:\d+(?:\.\d+)?|indefinite)', str(attrs['repeatCount'])):
                raise ValueError('artwork repeat count is unsupported')
        elif 'easing' in node['attrs']:
            raise ValueError('artwork easing is supported only on animation nodes')
        for child in node.get('children', []):
            visit(child, depth + 1)

    for node in artwork['nodes']:
        visit(node)


DESCRIPTIONS = {
    'site.header': 'Navigation supports variant=drawer: closed by default on every viewport, overlay trigger without a header band. menuLabel/closeLabel localize controls. presentation.scrollProgress=true adds a 180px vertical progress rail at right:30px after 40px of scrolling, hidden below 760px. presentation.scrollSnap=true enables proximity snapping between page sections. Existing brand/items/action remain editable.',
    'site.hero': 'variant=intro is an artwork-only opening with minimum height 100svh and artwork width min(440px,74vw); title stays accessible and does not appear as hero copy. artwork={viewBox,label,nodes:[{tag,attrs,children?}]} uses bounded SVG geometry and explicit SMIL animate/animateTransform. Animation attrs.easing="cubic-bezier(x1,y1,x2,y2)" supports exact overshoot for numeric from/to values; it is converted into native SMIL segments. Paint roles: accent, ink, muted. See data_schema.$defs.artworkNode. scrollCue enables the scroll marker with a 5.6s delay and 1.6s fade. No brand geometry is implied: supply it from the selected prototype.',
    'site.section': 'presentation.style=editorial uses minimum height 100svh, padding 72px 24px, centered narrative layout, 30ch title at clamp(24px,3.4vw,34px), 66ch body at 16.5px/1.75. lockup={src:<owned asset ID>,alt} or {artwork:<bounded SVG>} precedes the title at min(680px,92vw) with a 64px gap. Artwork can reproduce a reference wordmark including text and exact SVG coordinates without replacing assets. titleAccent is an exact title substring to color; emphasis contains exact paragraph phrases to render bold. tone=dark uses color.dark; light manifesto and divider surfaces use color.canvas. Text remains editable. variant=manifesto remains supported.',
    'site.cardGrid': 'variant=people with presentation.style=editorial uses centered unboxed 198px people cards, max width 1060px, gaps 46px 42px, names at 19px/300 and uppercase monospace roles at 9.5px. portrait.shape=circle clips images and monogram fallbacks to 124px circles, 96px below 480px, with a thin border. Preserve all existing people and asset IDs. presentation.reveal=stagger reveals entries on scroll with a 1s transition.',
    'site.footer': 'presentation.style=editorial provides a compact centered footer. Optional media.src uses an existing owned asset as its mark.',
}
DESCRIPTIONS['site.section'] += (' presentation.height=content removes the viewport minimum for short text; viewport retains it. '
    'presentation.paddingTop and paddingBottom set exact integer pixel spacing (0..240). Omitted properties preserve existing styling.')
DESCRIPTIONS['site.cardGrid'] += (' presentation.noteAlign=left|center|right aligns the note below the collection. '
    'presentation.paddingTop and paddingBottom set exact integer pixel spacing (0..240). Omitted properties preserve existing styling.')


def component_presentation_data(component_id, data):
    """Retain declared presentation through legacy record/form round trips.

    Consumers derive supported fields here rather than maintaining another
    independent allowlist. Unknown site data is not reclassified as styling.
    """
    from jsonschema import Draft202012Validator, ValidationError
    properties = presentation_properties(component_id)
    value = {key: deepcopy(data[key]) for key in properties if key in data}
    schema = {'type': 'object', 'properties': properties, '$defs': artwork_definitions(), 'additionalProperties': False}
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise ValueError('Invalid component presentation: ' + exc.message) from exc
    validate_artwork(value.get('artwork'))
    validate_artwork(value.get('lockup', {}).get('artwork'))
    return value
