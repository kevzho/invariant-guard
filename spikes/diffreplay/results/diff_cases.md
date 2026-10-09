# Functions with differences or nondeterminism (to label)

## C00 more-itertools f89d7a33 `more_itertools/more.py::ichunked`

commit: Issue 1304: make ichunked match chunked for n=0 and negative n  
classes: {'same': 33, 'different_return': 26}  
targets: [('more_itertools/more.py::ichunked', 'direct', 59)]  doc_only=False sig_changed=False

- **different_return** [test via ichunked] `ichunked([1, 2, 3], 0)`  
  base: `returned <iter generator: [] then raised ValueError: Stop argument for islice() must be None or an integer: 0 <= x <= sys.maxsize.>`  
  patch: `returned <iter generator: []>`
- **different_return** [test_new via ichunked] `ichunked(iter([1, 2, 3]), 0)`  
  base: `returned <iter generator: [] then raised ValueError: Stop argument for islice() must be None or an integer: 0 <= x <= sys.maxsize.>`  
  patch: `returned <iter generator: []>`
- **different_return** [test via ichunked] `ichunked(count(), -1)`  
  base: `returned <iter generator: [] then raised ValueError: Indices for islice() must be None or an integer: 0 <= x <= sys.maxsize.>`  
  patch: `returned <iter generator: [] then raised ValueError: n must be at least 0>`

## C01 more-itertools def2dabe `more_itertools/more.py::one`

commit: Fix one()/only() dropping a falsy user-supplied exception  
classes: {'same': 64, 'nondeterministic': 1}  
targets: [('more_itertools/more.py::one', 'direct', 65)]  doc_only=False sig_changed=False

- **nondeterministic** [stub via one] `one(frozenset({'\x16êÔûP\U000a8630', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', 'a¼û\U00016192', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only'}))`  
  base: `raised ValueError: Expected exactly one item in iterable, but got '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', 'a¼û\U00016192', and `  
  patch: `raised ValueError: Expected exactly one item in iterable, but got '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', and perhaps more`  
  base#2: `raised ValueError: Expected exactly one item in iterable, but got 'j\\\x87\U001007c0𤎠±à©Sð苎', '🏻', and perhaps more.`

## C02 more-itertools def2dabe `more_itertools/more.py::only`

commit: Fix one()/only() dropping a falsy user-supplied exception  
classes: {'same': 47, 'nondeterministic': 1}  
targets: [('more_itertools/more.py::only', 'direct', 48)]  doc_only=False sig_changed=False

- **nondeterministic** [stub via only] `only(frozenset({'\x16êÔûP\U000a8630', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', 'a¼û\U00016192', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only'}))`  
  base: `raised ValueError: Expected exactly one item in iterable, but got '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', 'a¼û\U00016192', and `  
  patch: `raised ValueError: Expected exactly one item in iterable, but got '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', and perhaps more`  
  base#2: `raised ValueError: Expected exactly one item in iterable, but got 'j\\\x87\U001007c0𤎠±à©Sð苎', '🏻', and perhaps more.`

## C03 more-itertools e426d256 `more_itertools/more.py::zip_broadcast`

commit: Reuse input iterators in zip_broadcast  
classes: {'same': 71, 'nondeterministic': 1}  
targets: [('more_itertools/more.py::zip_broadcast', 'direct', 72)]  doc_only=False sig_changed=False

- **nondeterministic** [stub via zip_broadcast] `zip_broadcast(frozenset({'', '*?J\U00050b17¸\x1b', '\x1b¶', '¤𠿳\x0f\xad3', '©!{EBñ\U000cfff9Ê\x8d\x1bé\U0008012f\U00102c97ãA¨s', '¹VKx', '¼S\x93ã\x8cÀß·%\x07\U00057295\U000ad7f8', 'Àé\U00046f96¨\x8bè9', 'Û', '𱊺', 'rüÆ¥®(`  
  base: `returned <iter generator: [('',), ('𱊺',), ('©!{EBñ\U000cfff9Ê\x8d\x1bé\U0008012f\U00102c97ãA¨s',), ('rüÆ¥®(F',), ('¹VKx',), ('¤𠿳\x0f\xad3',), ('Àé\U00046f96¨\x8bè9',), ('}p',), ('\x1b¶',), ('¼S\x93ã\x8cÀß·%\x07\U00057295\U000ad7f8',), ('*?J\U00050b17¸\x1b',), `  
  patch: `returned <iter generator: [('',), ('Û',), ('}p',), ('*?J\U00050b17¸\x1b',), ('¼S\x93ã\x8cÀß·%\x07\U00057295\U000ad7f8',), ('©!{EBñ\U000cfff9Ê\x8d\x1bé\U0008012f\U00102c97ãA¨s',), ('𱊺',), ('¹VKx',), ('Àé\U00046f96¨\x8bè9',), ('¤𠿳\x0f\xad3',), ('\x1b¶',), ('rüÆ¥`  
  base#2: `returned <iter generator: [('',), ('\x1b¶',), ('𱊺',), ('}p',), ('¤𠿳\x0f\xad3',), ('*?J\U00050b17¸\x1b',), ('Àé\U00046f96¨\x8bè9',), ('Û',), ('rüÆ¥®(F',), ('¼S\x93ã\x8cÀß·%\x07\U00057295\U000ad7f8',), `

## C04 more-itertools d032cab7 `more_itertools/more.py::constrained_batches`

commit: fix nonpositive item limits in constrained_batches  
classes: {'same': 64, 'different_return': 1}  
targets: [('more_itertools/more.py::constrained_batches', 'direct', 65)]  doc_only=False sig_changed=False

- **different_return** [shape via constrained_batches] `constrained_batches([' --1 1 -', '1- -----', ' 1 1', '1  -1---', '-', ''], 2, -1)`  
  base: `returned <iter generator: [] then raised ValueError: item size exceeds maximum size>`  
  patch: `returned <iter generator: [] then raised ValueError: maximum count must be greater than zero>`

## C05 more-itertools 1fd388bc `more_itertools/recipes.py::running_min`

commit: Validate running window sizes through the index protocol  
classes: {'same': 48, 'nondeterministic': 1}  
targets: [('more_itertools/recipes.py::running_min', 'direct', 49)]  doc_only=False sig_changed=False

- **nondeterministic** [stub via running_min] `running_min(frozenset({'\x16êÔûP\U000a8630', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', 'a¼û\U00016192', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only'}))`  
  base: `returned <iter itertools.accumulate: ['j\\\x87\U001007c0𤎠±à©Sð苎', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'j\\\x87\U001007c0𤎠±à©Sð苎', '\x16êÔûP\U000a8630', '\x16êÔûP\U000a8630', '\x16êÔûP\U000a8630']>`  
  patch: `returned <iter itertools.accumulate: ['a¼û\U00016192', 'a¼û\U00016192', 'a¼û\U00016192', 'a¼û\U00016192', 'a¼û\U00016192', '\x16êÔûP\U000a8630']>`  
  base#2: `returned <iter itertools.accumulate: ['a¼û\U00016192', 'a¼û\U00016192', 'a¼û\U00016192', 'a¼û\U00016192', '\x16êÔûP\U000a8630', '\x16êÔûP\U000a8630']>`

## C06 more-itertools 1fd388bc `more_itertools/recipes.py::running_max`

commit: Validate running window sizes through the index protocol  
classes: {'same': 48, 'nondeterministic': 1}  
targets: [('more_itertools/recipes.py::running_max', 'direct', 49)]  doc_only=False sig_changed=False

- **nondeterministic** [stub via running_max] `running_max(frozenset({'\x16êÔûP\U000a8630', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', 'a¼û\U00016192', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only'}))`  
  base: `returned <iter itertools.accumulate: ['j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only', '🏻', '🏻', '🏻', '🏻']>`  
  patch: `returned <iter itertools.accumulate: ['a¼û\U00016192', '🏻', '🏻', '🏻', '🏻', '🏻']>`  
  base#2: `returned <iter itertools.accumulate: ['a¼û\U00016192', '🏻', '🏻', '🏻', '🏻', '🏻']>`

## C07 more-itertools b8e0cbb8 `more_itertools/recipes.py::random_permutation`

commit: Add missing word in docstring  
classes: {'same': 20, 'nondeterministic': 1}  
targets: [('more_itertools/recipes.py::random_permutation', 'direct', 21)]  doc_only=True sig_changed=False

- **nondeterministic** [stub via random_permutation] `random_permutation(frozenset({'\x16êÔûP\U000a8630', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', '🏻', 'a¼û\U00016192', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only'}))`  
  base: `returned ('a¼û\U00016192', 'type_check_only', '🏻', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', 'j\\\x87\U001007c0𤎠±à©Sð苎', '\x16êÔûP\U000a8630')`  
  patch: `returned ('a¼û\U00016192', 'type_check_only', '\x16êÔûP\U000a8630', 'j\\\x87\U001007c0𤎠±à©Sð苎', '🏻', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90')`  
  base#2: `returned ('🏻', '÷\U0008378aê\ue1a6¼Xï\U0008f4fe(\x12\x01\U0006042d\U000cfbf4³K\U000c8545ã𧬾\U00069ec5\x90', 'a¼û\U00016192', '\x16êÔûP\U000a8630', 'j\\\x87\U001007c0𤎠±à©Sð苎', 'type_check_only')`

## C08 more-itertools caaec943 `more_itertools/more.py::random_ordered_combs_with_replacement`

commit: Add random ordered combinatoric functions  
classes: {'same': 12, 'different_return': 2}  
targets: [('more_itertools/more.py::random_ordered_combs_with_replacement', 'direct', 14)]  doc_only=False sig_changed=False

- **different_return** [stub via random_ordered_combs_with_replacement] `random_ordered_combs_with_replacement({}, 591764)`  
  base: `returned <iter generator: []>`  
  patch: `returned <iter generator: [()]>`
- **different_return** [stub via random_ordered_combs_with_replacement] `random_ordered_combs_with_replacement([], 2152354)`  
  base: `returned <iter generator: []>`  
  patch: `returned <iter generator: [()]>`

## C09 more-itertools dee22c93 `more_itertools/more.py::random_ordered_combs_with_replacement`

commit: Remove special case logic for cwr  
classes: {'same': 12, 'different_return': 2}  
targets: [('more_itertools/more.py::random_ordered_combs_with_replacement', 'direct', 14)]  doc_only=False sig_changed=False

- **different_return** [stub via random_ordered_combs_with_replacement] `random_ordered_combs_with_replacement({}, 591764)`  
  base: `returned <iter generator: [()]>`  
  patch: `returned <iter generator: []>`
- **different_return** [stub via random_ordered_combs_with_replacement] `random_ordered_combs_with_replacement([], 2152354)`  
  base: `returned <iter generator: [()]>`  
  patch: `returned <iter generator: []>`

## C10 more-itertools ce676d24 `more_itertools/more.py::split_before`

commit: Do not yield an empty list for an empty iterable when maxsplit is 0  
classes: {'same': 59, 'different_return': 1}  
targets: [('more_itertools/more.py::split_before', 'direct', 60)]  doc_only=False sig_changed=False

- **different_return** [defaults via split_before] `split_before([], lambda c: bool(c), maxsplit=0)`  
  base: `returned <iter generator: [[]]>`  
  patch: `returned <iter generator: []>`

## C11 more-itertools ce676d24 `more_itertools/more.py::split_when`

commit: Do not yield an empty list for an empty iterable when maxsplit is 0  
classes: {'same': 63, 'different_return': 2}  
targets: [('more_itertools/more.py::split_when', 'direct', 65)]  doc_only=False sig_changed=False

- **different_return** [defaults via split_when] `split_when('', lambda a, b: a != b, maxsplit=0)`  
  base: `returned <iter generator: [[]]>`  
  patch: `returned <iter generator: []>`
- **different_return** [shape via split_when] `split_when([], lambda x, y: x > y, maxsplit=0)`  
  base: `returned <iter generator: [[]]>`  
  patch: `returned <iter generator: []>`

## C12 boltons 4e5faa3d `boltons/ioutils.py::SpooledBytesIO.write`

commit: Fix spooled stream write compatibility and backwards text seeks (#513)  
classes: {'different_return': 4}  
targets: [('boltons/ioutils.py::SpooledBytesIO.write', 'direct', 4)]  doc_only=False sig_changed=False

- **different_return** [test via SpooledBytesIO.write] `SpooledBytesIO().write(bytes.fromhex('4920616d20657175616c21'))`  
  base: `returned None`  
  patch: `returned 11`
- **different_return** [test via SpooledBytesIO.write] `SpooledBytesIO().write(bytes.fromhex('4920616d206121'))`  
  base: `returned None`  
  patch: `returned 7`
- **different_return** [test via SpooledBytesIO.write] `SpooledBytesIO().write(bytes.fromhex('4920616d206221'))`  
  base: `returned None`  
  patch: `returned 7`

## C13 boltons 4e5faa3d `boltons/ioutils.py::SpooledStringIO.write`

commit: Fix spooled stream write compatibility and backwards text seeks (#513)  
classes: {'different_return': 59}  
targets: [('boltons/ioutils.py::SpooledStringIO.write', 'direct', 59)]  doc_only=False sig_changed=False

- **different_return** [test via SpooledStringIO.write] `SpooledStringIO().write('I am equal!')`  
  base: `returned None`  
  patch: `returned 11`
- **different_return** [test via SpooledStringIO.write] `SpooledStringIO().write('I am a!')`  
  base: `returned None`  
  patch: `returned 7`
- **different_return** [test via SpooledStringIO.write] `SpooledStringIO().write('I am b!')`  
  base: `returned None`  
  patch: `returned 7`

## C14 boltons 1216b467 `boltons/cacheutils.py::LRI.update`

commit: Complete September PR sweep for 26.2.0 (#481)  
classes: {'removed_exception': 15, 'same': 11}  
targets: [('boltons/cacheutils.py::LRI.update', 'direct', 26)]  doc_only=False sig_changed=False

- **removed_exception** [test_new via LRI.update] `LRI(values={'existing': 0}).update()`  
  base: `raised TypeError: LRI.update() missing 1 required positional argument: 'E'`  
  patch: `returned None`
- **removed_exception** [test_new via LRI.update] `LRI(values={'existing': 0}).update(a=1)`  
  base: `raised TypeError: LRI.update() missing 1 required positional argument: 'E'`  
  patch: `returned None`
- **removed_exception** [shape via LRI.update] `LRI(values={'existing': 0}).update(a=0)`  
  base: `raised TypeError: LRI.update() missing 1 required positional argument: 'E'`  
  patch: `returned None`

## C15 boltons 1216b467 `boltons/cacheutils.py::ThresholdCounter.update`

commit: Complete September PR sweep for 26.2.0 (#481)  
classes: {'same': 5, 'different_mutation': 45, 'internal_state_only': 1}  
targets: [('boltons/cacheutils.py::ThresholdCounter.update', 'direct', 51)]  doc_only=False sig_changed=False

- **different_mutation** [test_new via ThresholdCounter.update] `ThresholdCounter().update({'a': 3, 'b': 2, 'zero': 0})`  
  base: `returned None`  
  patch: `returned None`
- **different_mutation** [test_new via ThresholdCounter.update] `ThresholdCounter().update(['a'], a=3, b=2, zero=0)`  
  base: `returned None`  
  patch: `returned None`
- **different_mutation** [test_new via ThresholdCounter.update] `ThresholdCounter(threshold=0.1).update({'a': 10, 'b': 5})`  
  base: `returned None`  
  patch: `returned None`
- **internal_state_only** [shape via ThresholdCounter.update] `ThresholdCounter(threshold=0.1).update({'z': -5, 'b': 1, 'a': 2})`  
  base: `returned None`  
  patch: `returned None`

## C16 boltons 1216b467 `boltons/jsonutils.py::reverse_iter_lines`

commit: Complete September PR sweep for 26.2.0 (#481)  
classes: {'different_return': 13, 'same': 2}  
targets: [('boltons/jsonutils.py::reverse_iter_lines', 'direct', 15)]  doc_only=False sig_changed=False

- **different_return** [test_new via reverse_iter_lines] `reverse_iter_lines(BytesIO('café\nfin'.encode('latin-1')), encoding='latin-1')`  
  base: `returned <iter generator: ['fin'] then raised UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 3: unexpected end of data>`  
  patch: `returned <iter generator: ['fin', 'café']>`
- **different_return** [shape via reverse_iter_lines] `reverse_iter_lines(BytesIO('café\nfin'.encode('latin-1')), encoding='ni l--')`  
  base: `returned <iter generator: ['fin'] then raised UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 3: unexpected end of data>`  
  patch: `returned <iter generator: [] then raised LookupError: unknown encoding: ni l-->`
- **different_return** [shape via reverse_iter_lines] `reverse_iter_lines(BytesIO('café\nfin'.encode('latin-1')), encoding='na-n')`  
  base: `returned <iter generator: ['fin'] then raised UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 3: unexpected end of data>`  
  patch: `returned <iter generator: [] then raised LookupError: unknown encoding: na-n>`

## C17 boltons 1216b467 `boltons/strutils.py::bytes2human`

commit: Complete September PR sweep for 26.2.0 (#481)  
classes: {'same': 71, 'different_return': 3}  
targets: [('boltons/strutils.py::bytes2human', 'direct', 74)]  doc_only=True sig_changed=False

- **different_return** [doctest_new via bytes2human] `bytes2human(1024 ** 8)`  
  base: `returned '1024Z'`  
  patch: `returned '1Y'`
- **different_return** [defaults via bytes2human] `bytes2human(1024 ** 8, ndigits=1)`  
  base: `returned '1024.0Z'`  
  patch: `returned '1.0Y'`
- **different_return** [defaults via bytes2human] `bytes2human(1024 ** 8, ndigits=2)`  
  base: `returned '1024.00Z'`  
  patch: `returned '1.00Y'`

## C18 boltons 1216b467 `boltons/urlutils.py::URL.get_authority`

commit: Complete September PR sweep for 26.2.0 (#481)  
classes: {'different_return': 2, 'same': 11}  
targets: [('boltons/urlutils.py::URL.get_authority', 'direct', 13)]  doc_only=False sig_changed=False

- **different_return** [test_new via URL.get_authority] `URL('http://:secret@example.com/path').get_authority(with_userinfo=True)`  
  base: `returned 'example.com'`  
  patch: `returned ':secret@example.com'`
- **different_return** [defaults via URL.get_authority] `URL('http://:secret@example.com/path').get_authority(with_userinfo=True, full_quote=True)`  
  base: `returned 'example.com'`  
  patch: `returned ':secret@example.com'`

## C19 boltons 5eb6cc04 `boltons/mathutils.py::Bits.__getitem__`

commit: mathutils: resolve negative Bits indices (#450)  
classes: {'same': 28, 'different_return': 2, 'new_exception': 2}  
targets: [('boltons/mathutils.py::Bits.__getitem__', 'direct', 32)]  doc_only=False sig_changed=False

- **different_return** [test_new via Bits.__getitem__] `Bits('10').__getitem__(-2)`  
  base: `returned False`  
  patch: `returned True`
- **different_return** [test_new via Bits.__getitem__] `Bits('0000100').__getitem__(-3)`  
  base: `returned False`  
  patch: `returned True`
- **new_exception** [test_new via Bits.__getitem__] `Bits('10').__getitem__(-3)`  
  base: `returned False`  
  patch: `raised IndexError: -1`
- **new_exception** [shape via Bits.__getitem__] `Bits('10').__getitem__(-4)`  
  base: `returned False`  
  patch: `raised IndexError: -2`

## C20 boltons 8789df2c `boltons/timeutils.py::decimal_relative_time`

commit: timeutils: match timezone for default relative time (#469)  
classes: {'same': 21, 'nondeterministic': 4}  
targets: [('boltons/timeutils.py::decimal_relative_time', 'direct', 25)]  doc_only=False sig_changed=False

- **nondeterministic** [doctest via decimal_relative_time] `decimal_relative_time(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=0.002), datetime.now(timezone.utc).replace(tzinfo=None), ndigits=5)`  
  base: `returned (0.00207, 'seconds')`  
  patch: `returned (0.00256, 'seconds')`  
  base#2: `returned (0.00206, 'seconds')`
- **nondeterministic** [defaults via decimal_relative_time] `decimal_relative_time(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=0.002), datetime.now(timezone.utc).replace(tzinfo=None), ndigits=5, cardinalize=False)`  
  base: `returned (0.00203, 'second')`  
  patch: `returned (0.00204, 'second')`  
  base#2: `returned (0.00212, 'second')`
- **nondeterministic** [shape via decimal_relative_time] `decimal_relative_time(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=0.002), datetime.now(timezone.utc).replace(tzinfo=None), ndigits=8)`  
  base: `returned (0.00205, 'seconds')`  
  patch: `returned (0.002034, 'seconds')`  
  base#2: `returned (0.002081, 'seconds')`

## C21 boltons 970051b6 `boltons/namedutils.py::namedtuple`

commit: namedutils: reject empty type and field names (#462)  
classes: {'same': 10, 'different_exception': 5, 'exception_message': 36}  
targets: [('boltons/namedutils.py::namedtuple', 'direct', 51)]  doc_only=False sig_changed=False

- **different_exception** [shape via namedtuple] `namedtuple('', '')`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **different_exception** [shape via namedtuple] `namedtuple('', 'y y')`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **different_exception** [shape via namedtuple] `namedtuple('', '', rename=False)`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **exception_message** [shape via namedtuple] `namedtuple(' tiniitPnt', '')`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: ' tiniitPnt'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ' tiniitPnt'`
- **exception_message** [shape via namedtuple] `namedtuple(' tiniitPnt', 'x-y')`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: ' tiniitPnt'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ' tiniitPnt'`
- **exception_message** [shape via namedtuple] `namedtuple('o', 'yy-x  yy')`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: 'yy-x'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: 'yy-x'`

## C22 boltons 970051b6 `boltons/namedutils.py::namedlist`

commit: namedutils: reject empty type and field names (#462)  
classes: {'same': 12, 'different_exception': 4, 'exception_message': 17}  
targets: [('boltons/namedutils.py::namedlist', 'direct', 33)]  doc_only=False sig_changed=False

- **different_exception** [shape via namedlist] `namedlist('', '', rename=False)`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **different_exception** [shape via namedlist] `namedlist('', '-', rename=False)`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **different_exception** [shape via namedlist] `namedlist('', [])`  
  base: `raised IndexError: string index out of range`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: ''`
- **exception_message** [shape via namedlist] `namedlist('e -buauMluePtbibot', '', rename=False)`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: 'e -buauMluePtbibot'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: 'e -buauMluePtbibot'`
- **exception_message** [shape via namedlist] `namedlist('e -buauMluePtbibot', 'y,', rename=False)`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: 'e -buauMluePtbibot'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: 'e -buauMluePtbibot'`
- **exception_message** [shape via namedlist] `namedlist('lab uu-M l iPPllelnb', 'y x,xx y', rename=True)`  
  base: `raised ValueError: Type names and field names can only contain alphanumeric characters and underscores: 'lab uu-M l iPPllelnb'`  
  patch: `raised ValueError: Type names and field names must be non-empty and can only contain alphanumeric characters and underscores: 'lab uu-M l iPPllelnb'`

## C23 boltons 9decba3e `boltons/ecoutils.py::get_profile`

commit: ecoutils: skip identifying lookups when scrubbing (#461)  
classes: {'nondeterministic': 3}  
targets: [('boltons/ecoutils.py::get_profile', 'direct', 3)]  doc_only=False sig_changed=False

- **nondeterministic** [test via get_profile] `get_profile()`  
  base: `returned {'username': 'nobody', 'guid': '5abaa5f8e846b0c6d320f707adff69a', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machin`  
  patch: `returned {'username': 'nobody', 'guid': 'da7a75349ce1ea2f8e5fa2c05172723', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machin`  
  base#2: `returned {'username': 'nobody', 'guid': '82b3335b13e6f2614d45e84ed709a93', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit`
- **nondeterministic** [test via get_profile] `get_profile(scrub=True)`  
  base: `returned {'username': '-', 'guid': '5abaa5f8e846b0c6d320f707adff69a', 'hostname': '-', 'hostfqdn': '-', 'uname': {'system': 'Linux', 'node': '-', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machine': 'aarch64', 'processor'`  
  patch: `returned {'username': '-', 'guid': 'da7a75349ce1ea2f8e5fa2c05172723', 'hostname': '-', 'hostfqdn': '-', 'uname': {'system': 'Linux', 'node': '-', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machine': 'aarch64', 'processor'`  
  base#2: `returned {'username': '-', 'guid': '82b3335b13e6f2614d45e84ed709a93', 'hostname': '-', 'hostfqdn': '-', 'uname': {'system': 'Linux', 'node': '-', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed `
- **nondeterministic** [shape via get_profile] `get_profile(scrub=False)`  
  base: `returned {'username': 'nobody', 'guid': '5abaa5f8e846b0c6d320f707adff69a', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machin`  
  patch: `returned {'username': 'nobody', 'guid': 'da7a75349ce1ea2f8e5fa2c05172723', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit', 'version': '#1 SMP Wed May 13 14:27:36 UTC 2026', 'machin`  
  base#2: `returned {'username': 'nobody', 'guid': '82b3335b13e6f2614d45e84ed709a93', 'hostname': 'ig-probe', 'hostfqdn': 'ig-probe', 'uname': {'system': 'Linux', 'node': 'ig-probe', 'release': '6.12.76-linuxkit`

## C24 boltons a1ea1030 `boltons/dictutils.py::OrderedMultiDict.addlist`

commit: dictutils: preserve iterator values in addlist (#458)  
classes: {'same': 48, 'different_mutation': 15}  
targets: [('boltons/dictutils.py::OrderedMultiDict.addlist', 'direct', 63)]  doc_only=False sig_changed=False

- **different_mutation** [test_new via OrderedMultiDict.addlist] `OrderedMultiDict().addlist('a', iter([1, 2, 3]))`  
  base: `returned None`  
  patch: `returned None`
- **different_mutation** [test_new via OrderedMultiDict.addlist] `OrderedMultiDict().addlist('b', (x for x in [4, 5]))`  
  base: `returned None`  
  patch: `returned None`
- **different_mutation** [test_new via OrderedMultiDict.addlist] `OrderedMultiDict().addlist('a', (x for x in []))`  
  base: `returned None`  
  patch: `returned None`

## C25 boltons d45cc414 `boltons/urlutils.py::parse_qsl`

commit: urlutils: honor parse_qsl encoding (#455)  
classes: {'different_return': 4, 'same': 32, 'new_exception': 7}  
targets: [('boltons/urlutils.py::parse_qsl', 'direct', 43)]  doc_only=False sig_changed=False

- **different_return** [test_new via parse_qsl] `parse_qsl('%E9=v', encoding='latin-1')`  
  base: `returned [('�', 'v')]`  
  patch: `returned [('é', 'v')]`
- **different_return** [test_new via parse_qsl] `parse_qsl('k=%E9', encoding='latin-1')`  
  base: `returned [('k', '�')]`  
  patch: `returned [('k', 'é')]`
- **different_return** [defaults via parse_qsl] `parse_qsl('%E9=v', encoding='latin-1', keep_blank_values=False)`  
  base: `returned [('�', 'v')]`  
  patch: `returned [('é', 'v')]`
- **new_exception** [shape via parse_qsl] `parse_qsl('%', encoding='tt 1llnt-i-tli')`  
  base: `returned [('%', None)]`  
  patch: `raised LookupError: unknown encoding: tt 1llnt-i-tli`
- **new_exception** [shape via parse_qsl] `parse_qsl('%EEv', encoding='n-1')`  
  base: `returned [('�v', None)]`  
  patch: `raised LookupError: unknown encoding: n-1`
- **new_exception** [shape via parse_qsl] `parse_qsl(' v==9%vvvv', encoding='a ')`  
  base: `returned [(' v', '=9%vvvv')]`  
  patch: `raised LookupError: unknown encoding: a `

## C26 boltons 5df26592 `boltons/strutils.py::args2sh`

commit: Use the sep argument in args2sh and args2cmd (#454)  
classes: {'same': 36, 'different_return': 14}  
targets: [('boltons/strutils.py::args2sh', 'direct', 50)]  doc_only=False sig_changed=False

- **different_return** [test_new via args2sh] `args2sh(['aa', 'bb'], sep='|')`  
  base: `returned 'aa bb'`  
  patch: `returned 'aa|bb'`
- **different_return** [test_new via args2sh] `args2sh(['a a', 'bb'], sep='|')`  
  base: `returned "'a a' bb"`  
  patch: `returned "'a a'|bb"`
- **different_return** [defaults via args2sh] `args2sh(['aa', 'bb'], sep='')`  
  base: `returned 'aa bb'`  
  patch: `returned 'aabb'`

## C27 boltons 5df26592 `boltons/strutils.py::args2cmd`

commit: Use the sep argument in args2sh and args2cmd (#454)  
classes: {'same': 36, 'different_return': 14}  
targets: [('boltons/strutils.py::args2cmd', 'direct', 50)]  doc_only=False sig_changed=False

- **different_return** [test_new via args2cmd] `args2cmd(['aa', 'bb'], sep='|')`  
  base: `returned 'aa bb'`  
  patch: `returned 'aa|bb'`
- **different_return** [test_new via args2cmd] `args2cmd(['a a', 'bb'], sep='|')`  
  base: `returned '"a a" bb'`  
  patch: `returned '"a a"|bb'`
- **different_return** [defaults via args2cmd] `args2cmd(['aa', 'bb'], sep='')`  
  base: `returned 'aa bb'`  
  patch: `returned 'aabb'`

## C28 boltons c1c25da3 `boltons/mathutils.py::Bits.__init__`

commit: Fix off-by-one in Bits length check  
classes: {'same': 97, 'new_exception': 2}  
targets: [('boltons/mathutils.py::Bits', 'constructor', 99)]  doc_only=False sig_changed=False

- **new_exception** [test_new via Bits] `Bits(4, 2)`  
  base: `returned <boltons.mathutils.Bits val=4, len=2>`  
  patch: `raised ValueError: value 4 cannot be represented with 2 bits`
- **new_exception** [test_new via Bits] `Bits(1, 0)`  
  base: `returned <boltons.mathutils.Bits val=1, len=0>`  
  patch: `raised ValueError: value 1 cannot be represented with 0 bits`

## C29 boltons 3e9983db `boltons/iterutils.py::xfrange`

commit: iterutils: unify xfrange with frange's count-based semantics  
classes: {'different_return': 16, 'same': 51}  
targets: [('boltons/iterutils.py::xfrange', 'direct', 67)]  doc_only=False sig_changed=False

- **different_return** [test_new via xfrange] `xfrange(5, 0, step=-1.25)`  
  base: `returned <iter generator: []>`  
  patch: `returned <iter generator: [5.0, 3.75, 2.5, 1.25]>`
- **different_return** [test_new via xfrange] `xfrange(1, 5, step=-1)`  
  base: `returned <iter generator: [1.0, 0.0, -1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -7.0, -8.0, -9.0, -10.0, ...]>`  
  patch: `returned <iter generator: []>`
- **different_return** [test_new via xfrange] `xfrange(1.0, step=0.1)`  
  base: `returned <iter generator: [0.0, 0.1, 0.2, 0.30000000000000004, 0.4, 0.5, 0.6, 0.7, 0.7999999999999999, 0.8999999999999999, 0.9999999999999999]>`  
  patch: `returned <iter generator: [0.0, 0.1, 0.2, 0.30000000000000004, 0.4, 0.5, 0.6, 0.7, 0.7999999999999999, 0.8999999999999999]>`

## C30 boltons a180020c `boltons/setutils.py::IndexedSet.update`

commit: setutils: flatten inputs to IndexedSet.update (#474)  
classes: {'removed_exception': 16, 'different_mutation': 2}  
targets: [('boltons/setutils.py::IndexedSet.update', 'direct', 18)]  doc_only=False sig_changed=False

- **removed_exception** [test_new via IndexedSet.update] `IndexedSet([1]).update(list([2, 1, 3]), list([]), list([3, 4, 2]))`  
  base: `raised TypeError: unhashable type: 'list'`  
  patch: `returned None`
- **removed_exception** [test_new via IndexedSet.update] `IndexedSet().update([(1, 2)], [(3, 4), (1, 2)])`  
  base: `raised TypeError: unhashable type: 'list'`  
  patch: `returned None`
- **removed_exception** [shape via IndexedSet.update] `IndexedSet().update([], [])`  
  base: `raised TypeError: unhashable type: 'list'`  
  patch: `returned None`
- **different_mutation** [test_new via IndexedSet.update] `IndexedSet([1]).update(tuple([2, 1, 3]), tuple([]), tuple([3, 4, 2]))`  
  base: `returned None`  
  patch: `returned None`
- **different_mutation** [test_new via IndexedSet.update] `IndexedSet([1]).update(iter([2, 1, 3]), iter([]), iter([3, 4, 2]))`  
  base: `returned None`  
  patch: `returned None`

## C31 boltons e00c10a8 `boltons/listutils.py::BarrelList._translate_index`

commit: setutils, listutils: streamline bulk removal and fix list bounds (#440)  
classes: {'same': 80, 'exception_message': 7}  
targets: [('boltons/listutils.py::BarrelList._translate_index', 'direct', 0), ('boltons/listutils.py::BarrelList.insert', 'caller_d1', 37), ('boltons/listutils.py::BarrelList.pop', 'caller_d1', 49), ('boltons/listutils.py::BarrelList.__delitem__', 'caller_d1', 1)]  doc_only=False sig_changed=False

- **exception_message** [test via BarrelList.pop] `BarrelList().pop(50000)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `
- **exception_message** [shape via BarrelList.pop] `BarrelList().pop(0)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `
- **exception_message** [shape via BarrelList.pop] `BarrelList().pop(161)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `

## C32 boltons e00c10a8 `boltons/listutils.py::BarrelList.pop`

commit: setutils, listutils: streamline bulk removal and fix list bounds (#440)  
classes: {'exception_message': 7, 'same': 42}  
targets: [('boltons/listutils.py::BarrelList.pop', 'direct', 49)]  doc_only=False sig_changed=False

- **exception_message** [test via BarrelList.pop] `BarrelList().pop(50000)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `
- **exception_message** [shape via BarrelList.pop] `BarrelList().pop(0)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `
- **exception_message** [shape via BarrelList.pop] `BarrelList().pop(161)`  
  base: `raised IndexError: pop from empty list`  
  patch: `raised IndexError: `

## C33 boltons e00c10a8 `boltons/setutils.py::IndexedSet.intersection_update`

commit: setutils, listutils: streamline bulk removal and fix list bounds (#440)  
classes: {'different_mutation': 1}  
targets: [('boltons/setutils.py::IndexedSet.intersection_update', 'direct', 1)]  doc_only=False sig_changed=False

- **different_mutation** [test_new via IndexedSet.intersection_update] `IndexedSet(range(1000)).intersection_update(set(range(500, 2000)))`  
  base: `returned None`  
  patch: `returned None`

## C34 boltons e00c10a8 `boltons/setutils.py::IndexedSet.difference_update`

commit: setutils, listutils: streamline bulk removal and fix list bounds (#440)  
classes: {'different_mutation': 1, 'same': 14}  
targets: [('boltons/setutils.py::IndexedSet.difference_update', 'direct', 15)]  doc_only=False sig_changed=False

- **different_mutation** [test_new via IndexedSet.difference_update] `IndexedSet(range(1000)).difference_update(set(range(0, 1000, 2)))`  
  base: `returned None`  
  patch: `returned None`

## C35 dateutil 5cf3e3cb `dateutil/parser/isoparser.py::isoparser.parse_isodate`

commit: Decode input str when reporting an error in `parse_isodate`  
classes: {'exception_message': 7, 'same': 42}  
targets: [('dateutil/parser/isoparser.py::isoparser.parse_isodate', 'direct', 49)]  doc_only=False sig_changed=False

- **exception_message** [test_new via isoparser.parse_isodate] `isoparser().parse_isodate('2014-0423')`  
  base: `raised ValueError: String contains unknown ISO components: b'2014-0423'`  
  patch: `raised ValueError: String contains unknown ISO components: '2014-0423'`
- **exception_message** [test via isoparser.parse_isodate] `isoparser().parse_isodate('201404-23')`  
  base: `raised ValueError: String contains unknown ISO components: b'201404-23'`  
  patch: `raised ValueError: String contains unknown ISO components: '201404-23'`
- **exception_message** [test via isoparser.parse_isodate] `isoparser().parse_isodate('2014-04-19T')`  
  base: `raised ValueError: String contains unknown ISO components: b'2014-04-19T'`  
  patch: `raised ValueError: String contains unknown ISO components: '2014-04-19T'`

## C36 dateutil 34906dcd `dateutil/parser/isoparser.py::isoparser._parse_isotime`

commit: isoparse: Fail with inconsistent time separators  
classes: {'same': 168, 'new_exception': 2}  
targets: [('dateutil/parser/isoparser.py::isoparser._parse_isotime', 'direct', 0), ('dateutil/parser/isoparser.py::isoparser.isoparse', 'caller_d1', 102), ('dateutil/parser/isoparser.py::isoparser.parse_isotime', 'caller_d1', 68)]  doc_only=False sig_changed=False

- **new_exception** [test via isoparser.isoparse] `isoparser().isoparse('20120425T01:2000')`  
  base: `returned datetime(2012-04-25T01:20:00)`  
  patch: `raised ValueError: Inconsistent use of colon separator`
- **new_exception** [test via isoparser.parse_isotime] `isoparser().parse_isotime('14:3015')`  
  base: `returned time(14:30:15)`  
  patch: `raised ValueError: Inconsistent use of colon separator`

## C37 dateutil 79d2e486 `dateutil/parser/_parser.py::parser.parse`

commit: Fix TypeError in parser wrapper logic  
classes: {'same': 87, 'different_exception': 1}  
targets: [('dateutil/parser/_parser.py::parser.parse', 'direct', 0), ('dateutil/parser/_parser.py::parse', 'caller_d1', 88)]  doc_only=False sig_changed=False

- **different_exception** [test_new via parse] `parse('0-100')`  
  base: `raised TypeError: unsupported operand type(s) for +: 'int' and 'str'`  
  patch: `raised dateutil.parser._parser.ParserError: bad month number 0; must be 1-12: 0-100`
