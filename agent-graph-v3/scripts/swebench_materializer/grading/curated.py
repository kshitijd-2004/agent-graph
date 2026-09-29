"""Evaluator-only, source-reviewed concepts and independent answer probes.

Entries were authored from the frozen gold diffs, not mined from issue titles.
Each row contains a locus, two required concept patterns, and three prose probes.
The generator adds the patch digest and changed-file evidence automatically.
"""
ROWS = {}


def add(index, locus, concepts, gold, paraphrase, wrong):
    ROWS[index] = dict(locus=locus.split('|'), concepts=concepts.split('~'),
                       gold=gold, paraphrase=paraphrase, incorrect=wrong)

add(1, 'Collector.delete|can_fast_delete',
    r'fast.?delet|early return|single.instance~(clear|reset|set|assign).{0,65}(primary key|\bpk\b)|(?:primary key|\bpk\b).{0,65}(clear|reset|none|null)',
    'Collector.delete takes the single-instance fast-delete branch and returns before normal cleanup. Reset the instance primary key to None before this early return.',
    'The shortcut in deletion.py removes the database row but leaves the object identifier behind. The primary key needs clearing on that fast deletion path as well.',
    'Collector.delete fails because the transaction commits too early. Move all deletes into a larger atomic transaction; changing the object identifier is unnecessary.')
add(2, 'get_combinator_sql',
    r'clon|cop(y|ies|ied)|mutat|shared~quer(y|ies).{0,90}(select|column|values)|(?:select|column|values).{0,90}quer',
    'get_combinator_sql mutates a shared component query with set_values. Clone that query before changing its selected columns so later compilations do not inherit the selection.',
    'In compiler.py, assigning a projection alters the reused query object. Work on a copy before selecting columns, keeping other uses of the combination independent.',
    'get_combinator_sql must sort all component rows by primary key before combining them; the output ordering causes the reported failure.')
add(3, 'Field.__deepcopy__|__deepcopy__',
    r'error.messages|message dictionary|message mapping~shar|alias|independent|separate|cop(y|ies|ied)',
    'Field.__deepcopy__ shallow-copies the field but shares error_messages. Copy the error message dictionary so editing one field does not modify another.',
    'In fields.py cloned fields retain an alias to the message mapping. Give each clone an independent mapping to prevent error-message changes leaking across forms.',
    'Field.__deepcopy__ needs to deep-copy each validator because validator state is lost; the message mapping is already independent.')
add(4, '_save_table',
    r'raw|fixture.load|deserializ~(skip|exclud|disable|guard|bypass|not).{0,80}(insert|optimization)|(?:insert|optimization).{0,80}(raw|guard|exclud|disabl)',
    '_save_table forces an INSERT for a new object with a default primary key even during raw fixture loading. Guard that optimization with not raw so deserialization can update an existing row.',
    'In base.py the insert-only shortcut must exclude raw saves. Otherwise loading serialized data skips the ordinary update attempt and can collide with an existing key.',
    '_save_table should always allocate a fresh primary key on fixture loading and discard the explicit key in the serialized record.')
add(5, 'ChoicesMeta|do_not_call_in_templates',
    r'call|invok~do.not.call.in.templates|mark.{0,60}(non.callable|not.call|no.call)|prevent.{0,60}(call|invok)|disable.{0,60}(call|invok)',
    'The enum metaclass in enums.py leaves choices classes callable to template resolution. Set do_not_call_in_templates on the created class to prevent automatic invocation.',
    'ChoicesMeta must mark the enum class as not callable by templates. Resolving the class currently invokes it instead of letting the template access its members.',
    'ChoicesMeta should convert every enumeration value to a string before storing it; template escaping rejects numeric values.')
add(6, 'post_process',
    r'substitutions|substitution flag~initializ|unbound|undefined|before.{0,40}loop|outside.{0,40}loop',
    'post_process only initializes substitutions inside the loop. Initialize it to False before the loop so zero iterations cannot leave the variable unbound.',
    'In storage.py a zero pass count bypasses the assignment to the substitution flag. Give that flag an initial false value outside the loop before testing it later.',
    'post_process crashes because zero is used as a divisor while hashing names. Clamp the configured pass count to one to avoid division by zero.')
add(7, '_unregister_lookup|RegisterLookupMixin',
    r'class.lookups|stale|cached.{0,40}(lookup|registration)|subclass~invalidat|clear|evict|refresh',
    '_unregister_lookup deletes from class_lookups but leaves cached registrations usable. Call the cache invalidation helper after deletion so subsequent lookup resolution sees the removal.',
    'The stale lookup cache survives removal from the registry in query_utils.py. Clear cached lookup resolution, including subclass caches, when unregistering.',
    '_unregister_lookup should rename the lookup before deletion so the database can keep using the previous SQL operator.')
add(8, 'get_format',
    r'format.type|format name|format identifier|lazy.{0,30}(parameter|argument|object|value)~str\b|string|coerc|conver',
    'get_format uses a potentially lazy format_type as a settings attribute name. Convert the format identifier to str before lookup and cache-key construction.',
    'In formats.py resolve the lazy argument into a normal string before constructing the cache key or reading settings; an unevaluated proxy is not a valid attribute name.',
    'get_format should disable localization whenever the language is missing; translating the language name is what raises the exception.')
add(9, 'deferred_to_data',
    r'concrete.{0,30}model|underlying.{0,30}model~resolv|normaliz|use|follow|switch|travers|metadata',
    'deferred_to_data follows a relation to a proxy and uses the proxy metadata. Normalize cur_model to its concrete_model before retrieving opts and collecting required fields.',
    'In query.py relation traversal must switch to the underlying concrete model when determining deferred fields, so the primary-key and related-field metadata line up.',
    'deferred_to_data should load all deferred fields eagerly and remove support for combining only with related-object loading.')
add(10, 'Combinable|_connector_combinations|_connector_combinators',
    r'mod|modulo|remainder~(add|includ|register|missing|omit|extend).{0,100}(operator|type|combin|table|rule)|(?:operator|type|combin|table|rule).{0,100}(add|includ|missing|omit)',
    'The numeric type-combination table in expressions.py omits MOD from the arithmetic operators. Include modulo in these promotion rules so mixed numeric operands resolve an output type.',
    'Combinable remainder operations need the same numeric promotion rules as addition and division. Register the missing operator in the type inference table.',
    'Combinable should cast every remainder result to an integer because fractional modulo results are mathematically invalid.')
add(11, 'timesince',
    r'pivot|intermediate.{0,20}(date|time)~tzinfo|time.?zone|aware|naive',
    'timesince constructs a naive pivot datetime while the input is timezone-aware. Pass the input tzinfo to the pivot constructor so the subtraction uses compatible datetimes.',
    'The intermediate date in timesince.py loses time-zone information. Keep the original zone on the pivot rather than mixing an aware endpoint with a naive one.',
    'timesince should always divide the total seconds by thirty days; the error is caused by leap-year arithmetic in the month count.')
add(12, 'Apps.clear_cache|get_swappable_settings_name',
    r'get.swappable.settings.name|swappable.{0,40}(cache|memo)|(?:cache|memo).{0,40}swappable~invalidat|clear|stale|evict|reset',
    'Apps.clear_cache invalidates model caches but omits the memoized get_swappable_settings_name results. Clear that cache too when the app registry is reset to avoid stale settings-name resolution.',
    'In registry.py old swappable-model answers survive registry changes. Evict the swappable settings-name cache together with the other app caches.',
    'Apps.clear_cache should rebuild the database schema because swapping a model requires rewriting existing migration files.')
add(13, 'Aggregate.as_sql|as_sql',
    r'distinct~space|whitespace|separator|concatenat|token',
    'Aggregate.as_sql supplies DISTINCT without a trailing space. Add a separator before the argument expression so the filtered aggregate template cannot concatenate SQL tokens.',
    'In aggregates.py the distinct modifier runs into the following expression. Emit whitespace after that SQL keyword when constructing the aggregate.',
    'Aggregate.as_sql needs to place the filter predicate in a HAVING clause rather than the WHERE clause to make distinct counts work.')
add(14, 'FILE_UPLOAD_PERMISSIONS|global_settings.py',
    r'none|unset|umask|temporary|handler|system.dependent~0o644|0644|\b644\b|owner.{0,35}(write|read).{0,50}(other|group)',
    'FILE_UPLOAD_PERMISSIONS is None in global_settings.py, leaving the resulting mode dependent on upload handler and OS defaults. Set the default to 0o644 for consistent owner-writable, publicly readable uploads.',
    'The unset upload permission setting lets temporary-file handling and the umask produce different modes. In global_settings.py choose 0644 so uploaded files have a consistent default mode.',
    'FILE_UPLOAD_PERMISSIONS should default to 0777 so every process can modify uploads and the web server can execute them.')
add(15, 'RenameContentType._rename|_rename',
    r'save|persist|writ~using.?=.?db|database alias|same database|selected database|schema.editor|using argument',
    'RenameContentType._rename selects a database and opens a transaction there, but save omits using=db. Pass the same database alias to save so the renamed content type is persisted on the selected database.',
    'In contenttypes management the update can go to the default connection despite the schema editor using another database. Supply that selected database to the content-type save call.',
    'RenameContentType._rename should catch every database exception and silently keep the old model name; duplicate content types are the only source of this problem.')
add(16, 'Poly',
    r'priorit|dispatch|reflected|reverse.{0,20}multipli~higher|raise|increase|prefer|before|10\.001',
    'Poly lacks a higher operation priority, so Expr multiplication handles a left expression before polynomial dispatch. Raise Poly._op_priority above Expr to prefer its reflected arithmetic.',
    'In polytools.py polynomial arithmetic must win dispatch over ordinary expressions. Give Poly a higher priority so reverse multiplication runs when the polynomial is on the right.',
    'Poly should disable commutativity so multiplying it from the left and the right deliberately creates different symbolic products.')
add(17, '_eval_evalf',
    r'argument|self.args~evalf|numeric.{0,30}(evaluat|convert)|evaluat.{0,40}(numeric|before|recurs)|recurs.{0,40}evaluat',
    '_eval_evalf passes symbolic arguments directly to _imp_. Evaluate each argument numerically at the requested precision before invoking the implementation.',
    'In function.py the numerical fallback must recursively evaluate its inputs before calling the user implementation; _imp_ cannot reliably operate on unevaluated symbolic arguments.',
    '_eval_evalf should catch every exception from _imp_ and return zero rather than attempting evaluation.')
add(18, 'coth.eval|coth|hyperbolic.py',
    r'cotm|cothm|misspell|wrong variable|undefined.{0,20}(name|variable)~cothm|correct.{0,30}(name|variable)|rename|typo',
    'coth.eval computes cothm but tests the misspelled cotm against ComplexInfinity. Use cothm in that branch to inspect the value actually computed.',
    'The infinity check in hyperbolic.py references an undefined variable. Correct the typo to cothm, which holds the shifted coth result.',
    'coth.eval should convert every integer input to floating point before substitution because exact arithmetic cannot represent poles.')
add(19, '_eval_col_insert|col_insert',
    r'index|offset|column~subtract.{0,40}(only|twice|pos)|only.{0,40}(width|cols|column)|pos.{0,40}(twice|extra|again)|j.{0,12}other.cols',
    '_eval_col_insert subtracts both pos and other.cols when reading trailing original columns. Subtract only the inserted width: the original column index is j - other.cols.',
    'The tail-copy offset in common.py accounts for the insertion position twice. Shift the source column by only the number of inserted columns to preserve the original tail.',
    'col_insert should transpose the inserted matrix first because rows and columns are reversed in the supplied matrix.')
add(20, 'Vector.__add__|__add__',
    r'zero|\b0\b~return.{0,30}(self|original|unchanged)|identity|before.{0,40}(check|validat)',
    'Vector.__add__ sends zero into _check_vector, which rejects a scalar. Handle the additive identity first by returning self when other == 0.',
    'In vector.py special-case scalar zero before vector validation. Adding the identity should yield the original vector unchanged rather than rejecting its type.',
    'Vector.__add__ should turn every scalar into a vector with that value on all three axes, then add the components.')
add(21, 'DenseNDimArray._new|_new|_loop_size',
    r'empty.{0,30}(shape|product)|rank.?0|rank.zero|zero.dimensional~identity|initial.{0,30}(one|1)|product.{0,30}(one|1)|size.{0,30}(one|1)',
    'DenseNDimArray._new sets the loop size to zero for an empty shape. Use a product initialized to one so a rank-zero array contains one scalar element.',
    'In dense_ndim_array.py the empty shape must have product one, the multiplicative identity. The stored iteration size for a zero-dimensional array is one, not zero.',
    'DenseNDimArray._new must reject empty shapes because scalar arrays cannot have a meaningful length.')
add(22, 'Quaternion.to_rotation_matrix|to_rotation_matrix|m12',
    r'm12|row.{0,10}(1|one|second).{0,30}(2|two|third)|second.row.{0,25}third~minus|subtract|negative|sign',
    'Quaternion.to_rotation_matrix has the wrong sign in m12, the second-row third-column entry. Subtract b*a from c*d rather than adding it.',
    'In quaternion.py the off-diagonal m12 entry needs a minus sign on the scalar-times-first-vector-component term; the current addition breaks the rotation formula.',
    'Quaternion.to_rotation_matrix should transpose every output matrix because all off-diagonal entries are assigned to the wrong positions.')
add(23, 'MinMaxBase.__new__|MinMaxBase',
    r'empty|no.argument|zero.argument~identity|infinit|remove.{0,30}(raise|guard|exception)|allow|accept',
    'MinMaxBase.__new__ rejects empty arguments before the lattice logic can return an identity. Remove that guard so zero-argument Min and Max evaluate to their respective infinite identities.',
    'In miscellaneous.py allow the empty input through the normal lattice constructor. It already has identity elements: positive infinity for Min and negative infinity for Max.',
    'MinMaxBase should return zero for both empty Min and empty Max calls because no arguments contribute a value.')
add(24, 'Add._eval_is_zero|_eval_is_zero',
    r'\bnz\b|nonzero.{0,25}(list|term)|non.zero.{0,25}(list|term)|empty.{0,25}(term|list)~empty|zero.length|len.{0,12}0|return.{0,20}(none|unknown)|undecid',
    'Add._eval_is_zero reconstructs an empty nz list as zero and draws an invalid conclusion for complex terms. Treat an empty nonzero-term list as undecidable and return None.',
    'In add.py an empty nz collection is not evidence that the complex sum vanishes. Stop with an unknown result instead of rebuilding and testing that empty sum.',
    'Add._eval_is_zero should return True whenever all real parts cancel, regardless of any remaining imaginary component.')
add(25, 'posify',
    r'assumption|assumptions0~preserv|cop(y|ies|ied)|propagat|carry|retain|forward',
    'posify constructs positive Dummy symbols without the original assumptions. Forward assumptions0 when creating each replacement so finiteness and other known properties are retained.',
    'In simplify.py replacing a symbol to make it positive must preserve its existing assumptions. Carry those properties into the fresh dummy instead of throwing away finiteness information.',
    'posify should mark every generated symbol finite regardless of the original assumptions because all positive numbers are finite.')
add(26, 'morse|crypto.py',
    r'\bone\b|digit.{0,8}1|["\x27]1["\x27]~dot.{0,30}four.{0,10}dash|\.----|leading.{0,15}dot',
    'The Morse lookup in crypto.py maps digit 1 to four dashes without the initial dot. Use dot followed by four dashes so encoding and decoding one agree with Morse.',
    'The entry for one in the Morse table is missing a leading dot. Its correct sequence is .----; the reverse map must use that corrected key.',
    'The Morse encoder should reverse every sequence of dashes before decoding because messages are transmitted least-significant signal first.')
add(27, 'TR6|_TR56|_f|fu.py',
    r'exponent|rv.exp~real|order|compar',
    'The trigonometric power rewrite in fu.py compares an exponent to zero without knowing it is real. Skip the rewrite unless the exponent is real before performing ordered comparisons.',
    'In _TR56 the exponent can be complex, for which an ordering test is invalid. Guard the power transformation with a realness check and leave other powers unchanged.',
    'The trigonometric rewrite should replace every imaginary exponent with its absolute value so it can always compare exponents numerically.')
add(28, '_postprocessor|matexpr.py',
    r'matadd|matrix addition|additive|matrix sum~scalar.{0,25}zero|zero.{0,25}scalar|matrices.only|only.{0,25}matri|preserv.{0,35}(shape|matrix)',
    'The matrix postprocessor rebuilds MatAdd with a scalar zero from the empty nonmatrix arguments. For matrix addition, combine only the matrices so zero results retain their matrix shape.',
    'In matexpr.py the additive postprocessing branch must keep a matrix sum rather than inject an ordinary scalar zero. Rebuild MatAdd from matrices only to preserve shaped zeros.',
    'The matrix postprocessor should replace every ZeroMatrix with scalar zero before multiplication to reduce the number of block operations.')
add(29, 'diophantine',
    r'recurs|reorder|symbol.order~(pass|forward|propagat|preserv|carry).{0,65}permut|permut.{0,65}(pass|forward|propagat|preserv|lost|omit)',
    'diophantine recursively solves again when reordering symbols but drops permute. Forward permute=permute into that recursive call so all requested solutions survive reordering.',
    'The symbol-order branch in diophantine.py must preserve the permutation option on its recursive invocation. Otherwise the nested solve silently uses the default and loses solutions.',
    'diophantine should sort the final solution tuples numerically and discard duplicate signs before returning them.')
add(30, 'check_uri|linkcheck.py',
    r'raise.for.status|http.{0,30}status|status.{0,30}(check|error)|response.{0,30}status~before.{0,60}(anchor|pars)|anchor.{0,70}(before|status)|error.{0,35}(page|body)',
    'check_uri searches an HTTP error page for an anchor without checking the response status. Call raise_for_status before anchor parsing so the actual HTTP failure is reported.',
    'In linkcheck.py validate the response status before examining document fragments. Searching an error body can misreport a missing anchor instead of the network response error.',
    'check_uri should remove every fragment from URLs and report them as successful without fetching the page.')
add(31, 'record_typehints',
    r'signature~type.alias|alias.{0,30}(mapping|config)|configured.{0,20}alias',
    'record_typehints calls inspect.signature without the configured type aliases. Pass autodoc_type_aliases as type_aliases so description-mode annotations resolve through the alias mapping.',
    'In typehints.py signature extraction needs the same configured alias mapping as other autodoc paths; otherwise the recorded annotations expand the aliases instead of preserving them.',
    'record_typehints should remove all return annotations whenever description mode is enabled, because aliases cannot describe return values.')
add(32, 'get_object_members',
    r'none|absent|missing|unset~empty|truthiness|falsy|falsey',
    'get_object_members treats an empty __all__ like an absent one. Check explicitly for None instead of truthiness so an empty export list documents no members.',
    'In autodoc/__init__.py distinguish an unset export list from an intentionally empty list. Only the missing value should trigger implicit member discovery.',
    'get_object_members should always document all public names and ignore explicit export lists so every imported object is included.')
add(33, 'DocFieldTransformer.transform|transform',
    r'rsplit|right.{0,20}split|split.{0,30}(right|last)|last.{0,20}whitespace~type|argument|parameter|name',
    'DocFieldTransformer.transform splits the typed parameter at the first whitespace, cutting apart types with spaces. Split from the right once to separate the parameter name from the complete type.',
    'In docfields.py the final whitespace separates the name from its type expression. A right split preserves spaces inside a composite type instead of misclassifying the remaining type text as the argument name.',
    'DocFieldTransformer.transform should strip all punctuation from type annotations before rendering them as plain strings.')
add(34, 'collect_pages',
    r'collect.pages|page.{0,25}(generat|collect)|generat.{0,20}page~guard|skip|return|check|respect|disable',
    'collect_pages generates source pages from cached viewcode modules without checking the current builder. Skip singlehtml and disabled EPUB output in this page-generation hook.',
    'In viewcode.py guard page collection for the active builder, including checking the EPUB enable flag. Reusing the environment from an HTML build must not cause disabled source pages to be emitted.',
    'collect_pages should delete the entire build cache before every EPUB run because all cached documents are invalid in that format.')
add(35, 'PyMethod.get_index_text|get_index_text',
    r'property|properties~(remov|omit|without|drop).{0,40}(parenthes|parens|\(\))|(?:parenthes|parens).{0,40}(remov|omit|drop)',
    'PyMethod.get_index_text uses a callable-style name with parentheses in its property branch. Omit those parentheses for property index entries while retaining normal method formatting.',
    'In domains/python.py the property-specific index template should print the member name without parens; properties are accessed as attributes rather than calls.',
    'PyMethod.get_index_text should change properties into ordinary methods and remove their property metadata.')
add(36, 'Catalog.__iter__|__iter__',
    r'\bset\b|deduplicat|unique~source|line|position|location',
    'Catalog.__iter__ emits duplicate source-line positions from metadata records with different UUIDs. Deduplicate the source/line pairs and sort them before constructing each Message.',
    'In gettext.py location reporting needs unique source positions, not one entry per metadata UUID. Form a set of filename and line pairs and return those locations in stable order.',
    'Catalog.__iter__ should discard every location except the last one, since each translated message can only originate from one file.')
add(37, '_skip_member',
    r'unwrap|underlying.{0,20}(function|callable)|original.{0,20}(function|callable)~globals|global.namespace|owner|class',
    '_skip_member looks up the owning class in a decorator wrapper\'s globals. Unwrap the callable first and use the original function\'s globals to resolve the class.',
    'In napoleon/__init__.py the namespace of the wrapper can differ from the method owner. Resolve ownership through the underlying callable after unwrapping it.',
    '_skip_member should skip all decorated constructors because decorators make their signatures unusable for documentation.')
add(38, 'PyProperty.handle_signature|handle_signature',
    r'pars|cross.reference|xref|reference.node~annotation|type',
    'PyProperty.handle_signature inserts the type as plain text instead of parsing its annotation. Use _parse_annotation and insert the resulting nodes so cross references can be resolved.',
    'In domains/python.py property types need parsed annotation nodes, including reference nodes, rather than a literal text annotation. That lets the normal cross-reference machinery link the type.',
    'PyProperty.handle_signature should render annotations as escaped HTML strings so browsers automatically recognize class names as links.')
add(39, 'LiteralIncludeReader.read|dedent_filter',
    r'dedent|indentation~before.{0,55}(prepend|append)|(?:prepend|append).{0,55}(after|before.*dedent)|order',
    'LiteralIncludeReader.read applies dedent after prepend and append, so inserted lines affect indentation removal. Move dedent before both insertion filters.',
    'In directives/code.py normalize the included source indentation before prepending or appending synthetic lines. The filter order currently lets those additions change the dedent calculation.',
    'LiteralIncludeReader.read should remove all leading whitespace on every line, even inside nested code blocks, to align the output.')
add(40, 'make_glossary_term|StandardDomain|TokenXRefRole',
    r'lowercas|case.fold|case.sensitive|preserv.{0,25}case~register|registration|lookup|reference|xref|note.object',
    'Glossary registration lowercases term names and the term XRefRole lowercases lookups too. Preserve case in both registration and cross-reference resolution so differently cased terms stay distinct.',
    'In domains/std.py use case-sensitive glossary keys and references. Remove case folding at both the object-registration step and the term lookup step.',
    'make_glossary_term should merge every duplicate-looking term into one entry after lowercasing it, treating all spellings as aliases.')
add(41, '_MockObject.__getitem__|_make_subclass',
    r'key|subscript|type.argument~str\b|string|convert|coerc',
    '_MockObject.__getitem__ passes a non-string generic argument to _make_subclass as a name. Convert the key to str before constructing the mock subclass.',
    'In autodoc/mock.py subscription keys may be types rather than text. Stringify the key used as the generated class name so generic annotations can be mocked.',
    '_MockObject.__getitem__ should forbid subscription of mocked objects and always raise ImportError for generic annotations.')
add(42, 'LogisticRegressionCV.fit|LogisticRegressionCV',
    r'resolved|effective|local.{0,20}multi.class|multi.class.{0,30}(local|resolved)|auto~elasticnet|elastic.net|l1.ratio',
    'LogisticRegressionCV.fit uses self.multi_class rather than the resolved local mode to index coefficient paths with refit disabled. Use the effective mode, and only calculate l1_ratio_ for elasticnet; other penalties need None.',
    'In logistic.py the no-refit branch must use the resolved classification strategy for coefficient averaging. Also guard elastic-net ratio selection instead of indexing l1 ratios for penalties that have none.',
    'LogisticRegressionCV.fit should average all candidate coefficients indiscriminately and choose the first C value whenever refitting is disabled.')
add(43, 'clone',
    r'class|\btype\b|uninstantiated~instance|deepcopy|deep.copy|safe|reject',
    'clone sees get_params on an estimator class and incorrectly treats the class as an instance. Detect type objects and use the non-estimator path, which deep-copies when safe=False and rejects when safe=True.',
    'In base.py a class object is not a fitted or unfitted estimator instance. Handle such types through the ordinary safe/deepcopy branch rather than calling an unbound parameter method.',
    'clone should instantiate every class parameter without arguments so that get_params always has a receiver.')
add(44, '_BaseVoting.fit|fit|VotingClassifier',
    r'none|disabled|dropped~skip|ignore|before.{0,40}(check|validat)|exclude',
    '_BaseVoting.fit checks sample_weight support on disabled estimators that are None. Skip those entries before inspecting fit parameters, as they are not fitted.',
    'In voting.py exclude dropped estimators from sample-weight validation. A None entry has no fit signature and should be ignored instead of being inspected.',
    '_BaseVoting.fit should force all estimators to accept a sample_weight parameter by adding it dynamically to their fit methods.')
add(45, 'CountVectorizer.get_feature_names|get_feature_names',
    r'validat|initializ~vocabulary.|supplied.{0,25}vocabulary|fixed.{0,25}vocabulary',
    'CountVectorizer.get_feature_names checks vocabulary_ before it has been initialized from a supplied vocabulary. Validate the configured vocabulary when vocabulary_ is absent, then perform the normal check.',
    'In text.py feature-name retrieval needs to initialize the fixed vocabulary first. Calling the validation helper materializes vocabulary_ without requiring a fit on documents.',
    'CountVectorizer.get_feature_names should generate placeholder names from the alphabet whenever the vectorizer has not been fitted.')
add(46, 'HuberRegressor.fit|check_X_y',
    r'float|floating~dtype|cast|coerc|convert|validat',
    'HuberRegressor.fit leaves boolean X unchanged during validation, so downstream numeric operations fail. Request float64 or float32 dtype in check_X_y.',
    'In huber.py convert the input to a floating dtype during validation; boolean arrays cannot support the subtraction used by the robust-loss calculation.',
    'HuberRegressor.fit should turn off robust loss when the input is boolean and use classification accuracy as the objective.')
add(47, 'export_text|TREE_UNDEFINED',
    r'leaf|leaves|tree.undefined|sentinel|negative.{0,20}index~skip|guard|none|avoid|exclude|check',
    'export_text indexes feature_names with the TREE_UNDEFINED sentinel from leaf nodes. Guard that sentinel and use None for leaves instead of indexing the names list.',
    'In tree/export.py leaves have a negative sentinel rather than a feature index. Skip name lookup for those nodes so a small feature list is not indexed out of range.',
    'export_text should duplicate the first feature name until the list is as long as the number of tree nodes.')
add(48, 'ColumnTransformer.set_output|_safe_set_output',
    r'remainder~safe.set.output|forward|propagat|configur|apply|call',
    'ColumnTransformer.set_output configures named transformers but omits the remainder estimator. Forward the output configuration to that estimator too, excluding the drop and passthrough string sentinels.',
    'In _column_transformer.py propagate the requested container format to the remainder when it is an actual estimator. The special string modes should bypass that configuration call.',
    'ColumnTransformer.set_output should delete all remainder columns whenever a pandas output is requested.')
add(49, 'fowlkes_mallows_score',
    r'int64|64.bit|widen|integer.{0,25}overflow~ratio|separate.{0,25}(root|sqrt)|sqrt.{0,35}sqrt|avoid.{0,30}product',
    'fowlkes_mallows_score overflows integer contingency calculations and the pk*qk product. Widen counts to int64 and multiply square roots of the two ratios instead of forming the large product.',
    'In cluster/supervised.py use 64-bit contingency counts and compute the score from separate normalized ratios. Avoid the denominator product that overflows before its square root is taken.',
    'fowlkes_mallows_score should divide the input labels by their maximum so the label integers themselves never grow large.')
add(50, '_RepeatedSplits|_build_repr',
    r'repr|representation~cvargs|stored.{0,20}(argument|parameter)|cross.validation.{0,25}(argument|parameter)',
    '_RepeatedSplits lacks __repr__, and _build_repr misses parameters stored in cvargs. Delegate repr to the shared builder and read missing attributes from cvargs.',
    'In _split.py repeated splitters need the common representation builder, with a fallback to their stored cross-validation arguments for values such as n_splits.',
    '_RepeatedSplits should print the last generated train and test arrays as its representation instead of listing configuration parameters.')
add(51, 'strip_accents_unicode',
    r'combin|decompos|nfkd~ascii|early.return|fast.path|unchanged|equal',
    'strip_accents_unicode returns early when NFKD normalization leaves the string unchanged, even if combining marks remain. Use an ASCII-only fast path and filter combining characters for all other strings.',
    'In text.py already-decomposed input can still contain accent marks. Equality after normalization is not a valid early-return condition; restrict the shortcut to ASCII and otherwise remove combining marks.',
    'strip_accents_unicode should convert text to uppercase before normalization because lowercase accents cannot be decomposed.')
add(52, 'k_means',
    r'seed|random.state~same|both|shared|pre.?generat|before.{0,40}(branch|serial|parallel)|consistent',
    'k_means generates independent run seeds only in the parallel branch; the serial branch consumes the shared random state differently. Pre-generate seeds and use the same per-run seeds in both branches.',
    'In k_means_.py serial and parallel initializations need identical random seeds. Create the seed list before choosing execution mode, then pass each seed to its corresponding run.',
    'k_means should sort cluster labels after fitting because differently numbered clusters necessarily represent different centers.')
add(53, 'Axes.hist|hist_kwargs',
    r'hist.kwargs|keyword.{0,25}(dict|mapping)|existing.{0,25}(option|argument|range)|range~preserv|updat|overwrit|replac|discard|retain',
    'Axes.hist replaces hist_kwargs with a new density-only dictionary, discarding the range. Update the existing mapping with density instead of replacing it.',
    'In _axes.py preserve the existing histogram options when enabling density. Assign that flag into the current keyword dictionary so range and other arguments survive.',
    'Axes.hist should normalize the range endpoints by the total sample count whenever density is enabled.')
add(54, 'Axes3D.draw|draw',
    r'get.visible|visib|hidden~early.return|return|skip|guard|before|bypass',
    'Axes3D.draw does not check visibility before rendering. Add an early return when get_visible is false, before updating limits or drawing the background.',
    'In axes3d.py a hidden axes still enters draw. Guard the rendering method with its visibility state and skip all drawing when hidden.',
    'Axes3D.draw should make every child artist transparent while keeping the background visible when the axes is hidden.')
add(55, 'twinx|twiny',
    r'unit~cop(y|ies|ied)|inherit|propagat|share|assign|transfer',
    'twinx and twiny do not transfer units to the shared axis of the new twin. Copy the original x units for twinx and y units for twiny so unit conversion does not corrupt the limits.',
    'In _base.py the twin needs to inherit unit metadata on the dimension it shares. Propagate the original axis units when constructing either kind of twin.',
    'twinx should force both y axes to have the same data limits even though their values represent different quantities.')
add(56, 'AnchoredLocatorBase.__call__|__call__|_get_renderer',
    r'renderer.{0,35}(none|missing|absent)|(?:none|missing|absent).{0,35}renderer~figure|fallback|get.renderer',
    'The inset locator __call__ can receive renderer=None and passes it to extent calculation. Fetch a renderer from the owning figure as a fallback before computing the bounding box.',
    'In inset_locator.py obtain the figure renderer if none was supplied. Extent and offset computation need a real renderer even before the usual draw cycle.',
    'The inset locator should return a zero-sized bounding box whenever the renderer is unavailable, permanently hiding the inset.')
add(57, 'Figure.__getstate__|__getstate__',
    r'original.dpi|unscaled|logical.{0,15}dpi|base.{0,15}dpi~serializ|state|pickl|save|stor',
    'Figure.__getstate__ stores DPI already scaled for the device pixel ratio. Serialize the original unscaled DPI so restoring the figure does not apply the ratio twice.',
    'In figure.py save the logical DPI in the pickle state, using _original_dpi when available. A physical display-scaled value gets scaled again on restoration.',
    'Figure.__getstate__ should store twice the current DPI so displays with a higher pixel ratio receive enough pixels.')
add(58, 'Axes.__clear|__clear',
    r'old.children|removed.{0,25}(artist|child)|child|children|artist~(clear|reset|none|detach|unset).{0,60}(axes|figure|parent)|(?:axes|figure|parent).{0,60}(clear|reset|none|detach|unset)',
    'Axes.__clear discards the children list without clearing their parent references. Retain the old children temporarily and set each removed artist\'s axes and figure to None.',
    'In _base.py detaching children requires clearing both axes and figure links on the removed artists, not merely replacing the list that held them.',
    'Axes.__clear should keep removed artists attached to their original parents and only hide them so they can still receive events.')
add(59, 'DraggableBase|offsetbox.py',
    r'canvas~property|deriv|dynamic|on.demand|avoid.{0,35}(stor|attribute)|instead.{0,30}(stor|attribute)',
    'DraggableBase stores canvas as an instance attribute, pulling an unpicklable object into figure state. Expose canvas as a property derived from ref_artist.figure.canvas instead.',
    'In offsetbox.py resolve the canvas on demand through the reference artist. A computed property avoids serializing a stored canvas reference with draggable legends.',
    'DraggableBase should disable pickling for every figure that has a legend and raise an explicit exception instead.')
add(60, 'rc_context',
    r'backend~(exclud|omit|remov|skip).{0,65}(restor|snapshot|saved|state|copy)|(?:restor|snapshot|saved|state|copy).{0,65}(exclud|omit|remov|skip)',
    'rc_context saves and restores the backend setting, potentially restoring the unresolved sentinel and triggering backend switching later. Exclude backend from the saved configuration restored on exit.',
    'In matplotlib/__init__.py omit the backend key from the context snapshot. Restoring that key can cause a later backend lookup to switch backends and close existing figures.',
    'rc_context should call close on all figures at context exit so get_backend never encounters stale figure registrations.')
add(61, 'Patch.draw|_dash_pattern',
    r'dash.{0,15}(offset|pattern)|offset~zero|\b0\b|preserv|respect|retain|overrid',
    'Patch.draw replaces the dash offset with zero in the temporary dash pattern. Preserve the supplied offset when binding the draw function instead of overriding it.',
    'In patches.py drawing must retain the full configured dash pattern. Forcing its offset to zero discards the requested phase shift.',
    'Patch.draw should force solid lines for all filled patches because dash offsets are only meaningful for unfilled paths.')
add(62, '_make_image|LogNorm',
    r'zero|nonpositive|non.positive|<=.?0|less.{0,15}equal~epsilon|\beps\b|positive.{0,25}(minimum|bound|value)|clamp',
    '_make_image clamps negative LogNorm minima but permits zero. Clamp any nonpositive scaled vmin to the floating-point epsilon before passing it to LogNorm.',
    'In image.py the lower bound can round to zero, which is invalid for a logarithm. Include zero in the guard and replace it with a positive epsilon.',
    '_make_image should take the logarithm of the absolute value of every pixel so negative and zero values are always supported.')
add(63, '_cstack',
    r'right|submatrix|dependency.matrix~ones|\b1\b|preserv|cop(y|ies|ied)|actual',
    '_cstack fills the right block with ones instead of the supplied dependency matrix. Copy the actual right submatrix into that slice to preserve nested separability.',
    'In separable.py the right-hand block must retain its input dependency pattern. Replacing it by all ones falsely couples independent inputs of a nested compound model.',
    '_cstack should transpose every right-hand dependency matrix before combining it with the left side.')
add(64, 'TableDataDiff._diff|_diff',
    r'\bq\b|64.bit.{0,25}descriptor~variable.length|elementwise|element.wise|\bp\b|allclose',
    'TableDataDiff._diff handles variable-length P columns specially but not Q columns. Include Q descriptors in the elementwise variable-length-array comparison branch.',
    'In fits/diff.py Q-format variable-length cells need the same per-array comparison as P-format cells; ordinary object-array comparison incorrectly flags equal contents.',
    'TableDataDiff._diff should ignore all floating-point columns because their values cannot be compared reliably.')
add(65, 'quantity_input|wrapper|return_annotation',
    r'none~(skip|bypass|avoid|exclude).{0,60}(conver|unit)|(?:conver|unit).{0,60}(skip|bypass|avoid)|like.{0,30}(missing|empty)',
    'The quantity_input wrapper treats a None return annotation as a unit and invokes .to on the result. Treat None like an empty annotation and skip return-unit conversion.',
    'In units/decorators.py bypass unit conversion when the annotated return is None. A constructor returns no quantity, so attempting to convert it is invalid.',
    'The quantity_input wrapper should replace every None return value with a dimensionless quantity so callers always receive a quantity.')
add(66, 'world_to_pixel_values|SlicedLowLevelWCS',
    r'sliced.out|dropped|removed.{0,20}(world|dimension)|missing.{0,20}(world|coordinate)~pixel.to.world|forward.{0,25}(transform|conversion)|slice.{0,20}(position|value)|constant.{0,10}(one|1)|hard.cod',
    'world_to_pixel_values substitutes 1 for sliced-out world coordinates. Obtain their actual values from the forward pixel-to-world transform at the slice position instead of using that arbitrary constant.',
    'In sliced_wcs.py fill dropped world dimensions using the forward transform of the sliced pixel origin. A hard-coded one is not generally the world coordinate fixed by the slice.',
    'world_to_pixel_values should append zeros for all omitted world dimensions regardless of the slice location or projection.')
add(67, '_line_type|_get_tables_from_qdp_file',
    r'ignorecase|case.insensitive|case.fold|upper\(\)|normaliz.{0,20}case~\bno\b|mask|missing.value',
    'The QDP regex is case-sensitive and missing values are tested only against uppercase NO. Compile the parser case-insensitively and normalize NO tokens before masking.',
    'In qdp.py commands need case-insensitive matching, and the missing-value marker needs case folding too. Lowercase no should become a masked entry instead of a failed numeric conversion.',
    'The QDP reader should treat every unrecognized word as numeric zero and ignore malformed command lines.')
add(68, 'Card._split|_strg_comment_RE',
    r'anchor|end.of.(?:the.)?string|full.{0,15}(match|field)|complete.{0,15}(match|field)~unescap|double.{0,20}(quote|decod)|quote.{0,25}(twice|once|preserv)|preserv.{0,25}(escap|quote)',
    'Card parsing permits a partial string/comment match and unescapes doubled quotes again while splitting continued values. Anchor the regex to the end and preserve escaped quotes during _split so they are decoded only once.',
    'In card.py require a complete field match rather than accepting an early quote as the end. Keep doubled quotes intact when joining continuation segments to avoid double unescaping.',
    'Card._split should remove every quote character from a FITS string value before joining continuation cards.')
add(69, '_arithmetic_mask',
    r'operand.mask|operand.{0,35}(mask|unmasked)|other.{0,35}mask~none|missing|absent|copy|deepcopy',
    '_arithmetic_mask tests whether the operand itself is None instead of whether operand.mask is None. When the other mask is absent, copy self.mask rather than calling the mask combiner with None.',
    'In ndarithmetic.py an existing but unmasked operand needs the missing-mask branch. Check its mask, then copy the available mask so the combining function never receives an absent one.',
    '_arithmetic_mask should discard both masks whenever either operand is unmasked so the result is always unmasked.')
add(70, 'is_fits',
    r'filepath|extension|suffix~false|fall.?through|args\[0\]|empty.{0,20}(arg|tuple)|return.{0,25}(test|result)',
    'is_fits falls through to args[0] when a supplied path has a non-FITS extension. Return the suffix test result, including False, directly from the filepath branch.',
    'In fits/connect.py a negative extension check must return false. Falling through to the positional-object test can index an empty argument tuple.',
    'is_fits should accept all paths as FITS files regardless of extension so the registry never needs positional arguments.')
add(71, 'SkyCoord.__getattr__|__getattr__',
    r'getattribute|original.{0,25}(exception|error)|underlying.{0,25}(exception|error)|descriptor~delegat|call|preserv|propagat|mask',
    'SkyCoord.__getattr__ replaces an AttributeError from a subclass property with a generic missing-attribute message. Delegate to __getattribute__ to preserve the original descriptor exception.',
    'In sky_coordinate.py retry normal attribute access at the fallback so the underlying error from a property propagates instead of being masked by a fabricated missing-name error.',
    'SkyCoord.__getattr__ should return None for unknown names so subclass properties never raise AttributeError.')
add(72, 'Dataset.merge|dataset_merge_method',
    r'dataarray|data.array~to.dataset|convert.{0,50}dataset|wrap.{0,50}dataset|normaliz.{0,50}dataset',
    'Dataset.merge forwards a DataArray to the internal merge routine without converting it. Normalize a DataArray input with to_dataset before calling dataset_merge_method.',
    'In dataset.py convert an incoming data array into a dataset before the method-level merge, so its name and coordinates are interpreted as dataset variables.',
    'Dataset.merge should drop every coordinate from the incoming data array and concatenate only its raw numeric values.')
add(73, 'to_unstacked_dataset',
    r'sel\b|selection|selecting~drop.{0,45}(coordinate|variable|scalar)|drop.?=.?true|scalar.{0,40}(remov|drop)',
    'to_unstacked_dataset selects each variable but retains its scalar stacking coordinate. Use drop=True during sel, not only during squeeze, so reconstructed variables do not carry conflicting coordinates.',
    'In dataarray.py drop the variable-level coordinate at selection time. Squeezing afterwards cannot reliably remove that scalar label, which conflicts when rebuilding the dataset.',
    'to_unstacked_dataset should expand every variable to two dimensions before constructing the result, even when the original variable was one-dimensional.')
add(74, 'merge_attrs',
    r'dict|mapping|attribute~cop(y|ies|ied)|independent|alias|shar',
    'merge_attrs returns the first input dictionary directly in override mode. Return a new dict so mutating output attributes cannot modify the first input through an alias.',
    'In merge.py the override branch needs an independent copy of the attribute mapping. Reusing the same mutable dictionary makes the source and result share later changes.',
    'merge_attrs should clear attributes on every input before merging so there can be no conflicting metadata.')
add(75, '_LocIndexer.__getitem__|_LocIndexer',
    r'keyword|\*\*|unpack|reserved~dict|mapping|positional|indexer',
    '_LocIndexer.__getitem__ expands dimension labels as keyword arguments to sel, colliding with reserved parameters such as method. Pass the indexer dictionary positionally instead of unpacking it.',
    'In dataarray.py send the label mapping as one positional indexer to sel. Turning dimension names into keywords can accidentally set selection options rather than choose coordinates.',
    '_LocIndexer should forbid dimensions named after Python keywords and silently rename them before every selection.')
add(76, 'as_compatible_data',
    r'pandas|pd.series|pd.index|pd.dataframe~values|unwrap|extract',
    'as_compatible_data unwraps any object with a values attribute, which destroys unrelated array-like objects. Restrict values extraction to pandas Series, Index, and DataFrame inputs.',
    'In variable.py only known pandas containers should be unwrapped through values. Arbitrary objects can expose that attribute for a different purpose and must keep their original representation.',
    'as_compatible_data should coerce all objects with custom attributes to strings before assigning them into a variable.')
add(77, 'where|keep_attrs',
    r'getattr|fallback|empty.{0,20}(dict|mapping|attribute)|\{\}~\bx\b|second.{0,20}(argument|operand|input)|attrs',
    'where assumes the collected attrs list has an entry for its second argument. Retrieve x.attrs with an empty-dict fallback instead, since a scalar x has no attributes.',
    'In computation.py use the second operand as the attribute source and default to an empty mapping if it lacks attrs. Positional indexing into collected attributes fails for scalar inputs.',
    'where should always preserve the condition array attributes and discard attributes from both value operands.')
add(78, 'Coarsen.construct|should_be_coords',
    r'original.{0,20}coord|all.{0,20}coord|self.obj.coords|non.dimension|existing.{0,20}coord~preserv|retain|union|includ|set.coords|restor',
    'Coarsen.construct restores only coordinates present in window_dim. Include all original coordinate names in the set passed to set_coords so auxiliary coordinates are not demoted.',
    'In rolling.py preserve the full original coordinate set when constructing coarsened output. Restricting restoration to window dimensions drops non-dimensional coordinates into data variables.',
    'Coarsen.construct should delete auxiliary coordinates before reshaping because only dimension coordinates can be preserved.')
add(79, '_ensure_numeric|to_floatable',
    r'timedelta|duration~cast|astype|float|offset|datetime',
    '_ensure_numeric converts timedeltas using the datetime path with an absolute-time offset. Split the dtype cases and cast timedeltas directly to float while keeping datetime conversion for timestamps.',
    'In computation.py durations should become floating numeric magnitudes without a datetime origin. Use a direct numeric cast for timedelta data rather than timestamp normalization.',
    '_ensure_numeric should convert durations to calendar dates relative to the Unix epoch before polynomial evaluation.')
add(80, 'PandasMultiIndexingAdapter.__array__|__array__',
    r'dtype|data.type~self.dtype|stored|original|requested|cast|asarray',
    'PandasMultiIndexingAdapter.__array__ returns pandas level values without applying the adapter dtype. Default to self.dtype and pass that dtype to np.asarray for level extraction.',
    'In indexing.py convert extracted multi-index level values with the stored or explicitly requested dtype. Returning the pandas array directly loses the original narrower integer type.',
    'PandasMultiIndexingAdapter.__array__ should always return int64 because pandas cannot represent any narrower integer values.')
add(81, 'FDCapture|TextIOWrapper|capture.py',
    r'newline|universal.{0,20}(line|translat)|line.ending~empty|disable|preserv|translat|normaliz',
    'The capture text wrapper uses default newline translation, changing carriage returns. Set newline to the empty string to preserve original line endings in captured output.',
    'In capture.py disable universal-newline conversion in the text wrapper. An empty newline setting keeps carriage returns intact rather than normalizing them to line feeds.',
    'The capture reader should replace all carriage returns with spaces because terminal control characters are not valid text.')
add(82, 'visit',
    r'is.dir|directory.{0,25}check|direntry~follow.symlink|follow.{0,25}(link|symbolic)|symlink.{0,30}(true|follow)|default',
    'visit explicitly calls entry.is_dir with follow_symlinks=False, excluding linked directories from recursion. Use the symlink-following directory check before applying the recurse predicate.',
    'In pathlib.py directory traversal must follow symbolic links when testing directory entries. Restore the default is_dir behavior so linked test directories can be visited.',
    'visit should collect every regular file as a directory so symlink targets are treated the same as ordinary Python files.')
add(83, 'getmodpath',
    r'replace|substitut|rewrit~\.\[|parameter|bracket|literal|identifier',
    'getmodpath globally replaces .[ with [, which also rewrites literal parameter identifiers. Remove that replacement and return the joined module-path parts unchanged.',
    'In python.py the final substitution corrupts a bracket-containing parameter name. Joining the path segments is sufficient; do not rewrite dot-bracket sequences inside identifiers.',
    'getmodpath should strip every punctuation character from parameter IDs so the headline contains only letters and digits.')
add(84, 'pytest_runtest_makereport',
    r'independent|separate|elif|unconditional|outside~skip|location|xfail|branch',
    'pytest_runtest_makereport puts skip-location correction in an elif tied to xfail handling. Make it an independent if so runxfail does not bypass location adjustment for skipped tests.',
    'In skipping.py evaluate the skip-report location fix separately from the expected-failure branch. The conditional chain currently prevents that adjustment when runxfail is active.',
    'pytest_runtest_makereport should execute all skipped tests when runxfail is enabled and report their assertion locations.')
add(85, 'EncodedFile.mode|EncodedFile',
    r'buffer|underlying|delegat|property~mode|binary|\bb\b',
    'EncodedFile delegates mode to its binary buffer, even though the wrapper exposes text. Add a mode property that removes b from the underlying mode string.',
    'In capture.py report a text-mode value for the EncodedFile wrapper instead of forwarding the buffer\'s binary mode. A dedicated property can strip the binary flag.',
    'EncodedFile should encode every write twice because its buffer is binary and its callers already supply text.')
add(86, 'LogCaptureFixture|_finalize|set_level',
    r'handler.{0,25}level|level.{0,25}handler~(sav|remember|record|initial|original).{0,160}(restor|teardown|finaliz)|(?:restor|teardown|finaliz).{0,160}(sav|remember|record|initial|original)',
    'LogCaptureFixture restores logger levels but not the capture handler level changed by set_level. Save the handler level before changing it and restore it during _finalize.',
    'In logging.py remember the original capture-handler level as well as named-logger levels. Teardown must restore that saved handler threshold to prevent leaking it into another test.',
    'LogCaptureFixture should reset the root logger to DEBUG after every test so no later log messages are filtered out.')
add(87, 'TestCaseFunction.runtest|runtest',
    r'class|parent.obj|parent~skip|skipped',
    'TestCaseFunction.runtest checks method-level skipping but misses a skipped parent class before deferring tearDown for pdb. Check both class and method skip status before installing the delayed teardown.',
    'In unittest.py the debugger teardown workaround must consider the parent class skip flag too. A class-level skip should prevent scheduling tearDown even if the individual method is not marked.',
    'TestCaseFunction.runtest should always execute tearDown under pdb, even when the entire test class has been skipped.')
add(88, 'create_new_paste',
    r'lexer|syntax.{0,20}(mode|highlight)~plain.text|\btext\b|version.independent|supported',
    'create_new_paste chooses a version-specific Python lexer the paste service rejects. Send the supported text lexer instead so report uploads do not depend on Python-version highlighting names.',
    'In pastebin.py request plain-text formatting for the uploaded report. A text lexer avoids sending an unsupported syntax-highlighting identifier to the service.',
    'create_new_paste should switch from POST to GET and put the complete test report in the URL query string.')
add(89, 'PreparedRequest.prepare_url|prepare_url',
    r'leading.{0,20}(dot|period)|start.{0,20}(dot|period)|empty.{0,20}(label|host)|host.startswith~invalidurl|reject|validat',
    'PreparedRequest.prepare_url rejects a leading wildcard but not a leading dot in an ASCII host. Reject that empty first label with InvalidURL before IDNA handling can raise UnicodeError.',
    'In models.py validate ASCII hosts that start with a period. Such an empty label should produce InvalidURL just like a wildcard hostname, rather than escaping as an encoding error.',
    'PreparedRequest.prepare_url should silently remove every leading dot from the hostname and send the request to the altered domain.')
add(90, 'prepare_content_length',
    r'get|head~empty.body|bodyless|no.body|body.{0,20}none|zero.{0,20}(header|length)|length.{0,20}(zero|0)|unconditional',
    'prepare_content_length initializes Content-Length to zero unconditionally, including bodyless GET and HEAD. Set a zero length only for other methods when the body is None; keep real body lengths as before.',
    'In models.py do not synthesize a Content-Length header for GET or HEAD without a body. Move the zero-length default into the other-methods branch rather than assigning it up front.',
    'prepare_content_length should remove Content-Length from all requests, including POST requests with nonempty bodies.')
add(91, 'prepend_scheme_if_needed',
    r'auth|credential|userinfo~netloc|authority|reassembl|reconstruct|reattach|restore',
    'prepend_scheme_if_needed rebuilds the URL from netloc but parse_url stores auth separately. Reattach auth to the authority before reconstruction so proxy credentials are preserved.',
    'In utils.py URL normalization drops userinfo because credentials are not part of the parsed netloc. Restore that auth component when reassembling the scheme-prefixed URL.',
    'prepend_scheme_if_needed should remove proxy credentials from every URL and send them to the destination server as an Authorization header.')
add(92, '_encode_params|prepare_url',
    r'bytes|binary|payload~unchanged|preserv|raw|decod|native.string',
    '_encode_params converts byte payloads to native strings and can fail decoding binary bodies. Return string/bytes bodies unchanged and restrict native-string conversion to URL query parameters in prepare_url.',
    'In models.py keep binary request data as raw bytes. Text coercion belongs in URL preparation for query parameters, not in the shared encoder that also handles body payloads.',
    'The request encoder should base64-encode every binary body and every URL parameter automatically before sending them.')
add(93, 'merge_setting',
    r'none|null~merged|session|combined|all.{0,20}(entry|entries|value|setting)',
    'merge_setting removes None entries only from request overrides, leaving None values inherited from the session. Filter all None values from the merged mapping before returning it.',
    'In sessions.py inspect the combined settings, including session defaults, and drop entries whose value is None. Filtering only explicit request keys misses inherited null headers.',
    'merge_setting should serialize None as the literal string None so the server can decide whether to remove the header.')
add(94, 'Similar.run|run|similar.py',
    r'min.lines|threshold|minimum.{0,20}line~early.return|short.circuit|before.{0,40}(comput|scan)|skip.{0,40}(comput|scan)|return',
    'Similar.run still computes similarities when min_lines is zero. Short-circuit before _compute_sims and display so zero acts as a disabled-check sentinel.',
    'In similar.py a zero minimum-line threshold needs an early return before the similarity computation. Otherwise zero makes every block a candidate instead of turning the check off.',
    'Similar.run should interpret zero as requiring exact equality of complete files and continue scanning all input files.')
add(95, 'modify_sys_path',
    r'current.directory|\bcwd\b|empty.string|working.directory~guard|only|check|conditional|match',
    'modify_sys_path unconditionally pops sys.path[0], which can be a legitimate import path under runpy. Remove it only when it represents the current directory: empty string, dot, or cwd.',
    'In pylint/__init__.py guard the first path removal by checking that the entry denotes the working directory. A different import directory must remain on sys.path.',
    'modify_sys_path should remove every entry in PYTHONPATH before running lint checks to make imports deterministic.')
add(96, '_query_cpu',
    r'fraction|round|truncat|zero|\b0\b~clamp|at.least.one|minimum.{0,15}(one|1)|one.{0,15}(cpu|worker|process)|return.{0,10}1',
    '_query_cpu truncates fractional CPU shares to zero, yielding an invalid process count. Clamp the computed availability to at least one worker for sub-core allocations.',
    'In lint/run.py a pod with less than a full core can round down to zero CPUs. Use a minimum of one process since multiprocessing cannot launch a fractional worker.',
    '_query_cpu should use all host CPUs whenever the pod reports a fractional quota, ignoring the container allocation.')
add(97, '_is_ignored_file',
    r'normaliz|normpath|canonical~path|element|separator|match',
    '_is_ignored_file matches unnormalized paths, so recursive traversal forms can miss ignore expressions. Normalize element with os.path.normpath before basename and path-regex checks.',
    'In expand_modules.py canonicalize the incoming path before matching ignore rules. Equivalent paths with redundant components or different separators must be compared consistently.',
    '_is_ignored_file should disable ignore-path expressions during recursive runs because directory traversal cannot safely use regular expressions.')
add(98, '_store_type_annotation_node',
    r'attribute|qualified|dotted~recurs|travers|base.{0,20}(name|expression)|\.expr|receiver',
    '_store_type_annotation_node skips astroid.Attribute nodes in type comments. Recurse into the attribute base expression so the imported module name is recorded as used.',
    'In variables.py traverse the base of a qualified annotation rather than ignoring dotted types. Recording that receiver name prevents the imported module being flagged unused.',
    '_store_type_annotation_node should mark every import as used whenever any type comment is present in the file.')
add(99, '_finalize_figure|Nominal',
    r'grid~half|0\.5|\.5|invert|revers|categor.{0,20}(bound|limit)|tick.{0,20}limit',
    '_finalize_figure treats nominal axes like numeric ones. Disable their grids and use half-step category bounds when no explicit limits were given, reversing the y bounds for categorical order.',
    'In _core/plot.py nominal scales need categorical finishing: no grid lines, half-unit padding around categories, and an inverted vertical category order, while preserving user limits.',
    '_finalize_figure should sort nominal categories numerically and display a logarithmic axis with equally spaced grid lines.')
add(100, 'Blueprint.__init__|Blueprint',
    r'empty|falsy|falsey|not.name~valueerror|raise|reject|validat|guard',
    'Blueprint.__init__ rejects dots in names but accepts an empty string. Add an empty-name guard that raises ValueError during construction before registration can create ambiguous endpoint names.',
    'In blueprints.py reject a falsy name at construction time with ValueError. The existing dot check does not catch an empty identifier.',
    'Blueprint.__init__ should accept the empty name and silently substitute the application name at registration time.')

# Source descriptions are accepted alongside Python identifiers. These do not
# earn credit without every required causal/change concept.
for index, aliases in {
    15: ['contenttypes management'], 32: ['autodoc/__init__.py'],
    37: ['napoleon/__init__.py'], 40: ['glossary registration'],
    56: ['inset locator'], 60: ['matplotlib/__init__.py'],
    67: ['QDP regex', 'QDP parser'], 68: ['Card parsing'],
    77: ['where'], 81: ['capture text wrapper'],
}.items():
    ROWS[index]['locus'].extend(aliases)

# Require propositions, not just the names of the objects involved. These
# refinements reject plausible diagnoses that mention the right objects but
# propose a different change.
ROWS[3]['concepts'][1] = r'(?:cop(?:y|ies|ied)|independent|separate|alias|shar).{0,55}(?:error.messages|message mapping|message dictionary)|(?:error.messages|message mapping|message dictionary).{0,55}(?:copied|aliased|shared)'
ROWS[3]['reject'] = [r'(?:message mapping|error.messages|message dictionary).{0,25}already independent']
ROWS[39]['concepts'][1] += r'|before.{0,25}(?:both )?insertion'
ROWS[52]['concepts'][1] += r'|identical'
ROWS[58]['concepts'][1] = r'(?:clear|reset|detach|unset|set).{0,50}(?:their |both |each |removed )?(?:axes|figure|parent).{0,40}(?:none|links|references)|(?:clearing|resetting).{0,60}(?:axes|figure|parent)|(?:axes|figure).{0,40}(?:to none|= none)'
ROWS[75]['concepts'].append(r'(?:pass|send|supply|provide).{0,100}(?:dict|mapping|indexer)|positional')
ROWS[85]['concepts'].append(r'(?:remov|strip|text.mode|text mode|binary flag)')
ROWS[87]['concepts'].append(r'(?:check|consider|guard|prevent|before|both).{0,100}(?:skip|teardown|class|parent)|(?:class|parent).{0,60}(?:check|consider|guard|prevent)')
ROWS[87]['reject'] = [r'always execute teardown|execute teardown.{0,45}(?:even when|despite).{0,35}skip']
ROWS[27]['concepts'].append(r'(?:guard|check|unless|only|skip|leave).{0,100}(?:real|comparison|rewrite|transformation|powers)|realness check')
ROWS[93]['concepts'].append(r'(?:filter|drop|remov|discard|exclude).{0,60}(?:none|null)|(?:none|null).{0,40}(?:value|entries).{0,40}(?:filter|drop|remov)')
