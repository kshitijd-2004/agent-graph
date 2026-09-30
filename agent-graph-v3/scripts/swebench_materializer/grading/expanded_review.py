"""Patch-reviewed explanations for the frozen added cohort, keyed by instance.

Concepts and counterexamples are authored from the exact draft gold diffs.
The repair exporter pins these records to full patch hashes before use.
"""
REVIEWS = {}


def review(instance, locus, concepts, gold, paraphrase, incorrect, reject=''):
    if instance in REVIEWS:
        raise ValueError('Duplicate review: ' + instance)
    REVIEWS[instance] = dict(
        instance_id=instance,
        locus=locus.split('|'),
        concepts=concepts.split('~'),
        gold=gold,
        paraphrase=paraphrase,
        incorrect=incorrect,
        reject=reject.split('~') if reject else [],
    )


review('scikit-learn__scikit-learn-14141', '_get_deps_info',
    r'joblib~(?:add|includ|list|report|collect).{0,70}(?:dependenc|version)|(?:dependenc|version).{0,70}(?:add|includ|list|report|collect)',
    'In _get_deps_info the dependency list omits joblib, so diagnostic output cannot report its version. Include joblib among the dependencies whose versions are collected.',
    'The version inventory in _show_versions.py never visits joblib. Add that package to the dependency enumeration so environment diagnostics include its installed release.',
    'The dependency reporter imports joblib too early. Defer importing the entire dependency reporter until the first parallel operation.',
    reject=r'defer.{0,40}import|delay.{0,40}import')
review('astropy__astropy-13453', 'HTML.write',
       r'(?:data.cols|data columns|column list).{0,65}(?:set|assign|initializ)|(?:set|assign|initializ).{0,65}(?:data.cols|data columns|column list)~(?:apply|set|initializ|call).{0,60}(?:format|_set_col_formats)',
       'HTML.write sets header columns but not the data columns or their formats. Assign the data column list and call _set_col_formats before rendering cells so requested column formatting is used.',
       'In html.py initialize the data columns as well as the header, then apply column formats before producing the table. Otherwise cell rendering misses the supplied formatting settings.',
       'HTML.write escapes percent signs incorrectly. Disable HTML escaping for every numeric cell to preserve the requested display.')
review('django__django-10097', 'URLValidator',
       r'user|password|credential|auth~(?:exclud|reject|disallow|forbid|restrict).{0,90}(?:colon|slash|@|delimiter)',
       'URLValidator allows arbitrary non-whitespace in credentials, which can swallow authority delimiters. Restrict username and password character classes to exclude colon, slash and @ as well as whitespace.',
       'In validators.py the authentication regex is too permissive: user information can consume URL separators. Reject slash, @ and colon inside credential components so invalid authorities cannot masquerade as valid hosts.',
       'URLValidator rejects international hostnames because IDNA conversion is missing. Convert every hostname to ASCII before validating it.')
review('django__django-11119', 'Engine.render_to_string',
       r'autoescap~(?:pass|propagat|inherit|use|forward).{0,70}(?:engine|setting|context)|(?:engine|setting).{0,70}(?:pass|propagat|inherit|context)',
       'Engine.render_to_string constructs Context with its default autoescape setting. Pass the engine autoescape setting into the new Context so dictionary rendering honors the engine configuration.',
       'In engine.py a mapping is wrapped in a context without inheriting the engine escaping policy. Forward that autoescape configuration when constructing the context.',
       'Engine.render_to_string caches an escaped template indefinitely. Clear the template cache after each render to prevent double escaping.')
review('django__django-11133', 'HttpResponse.make_bytes',
       r'memoryview|memory view~(?:bytes|byte conversion|raw bytes).{0,75}(?:convert|direct|use|instead)|(?:convert|treat|handle).{0,75}(?:bytes|byte sequence)',
       'HttpResponse.make_bytes stringifies memoryview objects instead of returning their contents. Handle memoryview alongside bytes and convert directly with bytes(value), avoiding the object representation.',
       'In response.py a memory view falls through to string encoding. Treat it as a byte sequence and use direct bytes conversion so the response contains the buffer contents.',
       'HttpResponse.make_bytes uses the wrong response charset. Always encode every response with UTF-16 to preserve buffer data.')
review('django__django-11163', 'model_to_dict',
       r'empty|\[\]~(?:none|unspecified).{0,75}(?:distinguish|instead|only|explicit)|(?:distinguish|check|test|only).{0,75}(?:none|unspecified)',
       'model_to_dict tests fields by truthiness, treating an empty list like an unspecified selection. Check fields is not None so an explicitly empty selection excludes every field.',
       'In models.py distinguish an empty fields collection from None. Only an unspecified selection should allow all model fields; the empty collection should produce an empty dictionary.',
       'model_to_dict loses many-to-many ordering. Sort every relation by its primary key before returning the dictionary.')
review('django__django-11299', 'Query._add_q',
       r'simple.col|column.{0,25}(mode|flag)~(?:pass|forward|propagat).{0,65}(?:recurs|nested|child)',
       'Query._add_q drops simple_col when recursing into child nodes. Forward the column-mode flag to the recursive call so nested predicates use the same column rendering mode.',
       'In query.py pass simple_col through nested Q traversal. The outer compiler setting is otherwise lost in child conditions, producing inconsistent column references.',
       'Query._add_q should flatten all nested conditions into a single AND group to remove redundant parentheses.')
review('django__django-11451', 'ModelBackend.authenticate',
       r'username|credential~(?:password|credential).{0,85}(?:none|missing|absent)|(?:none|missing|absent).{0,85}(?:password|credential)~return|short.circuit|skip.{0,35}(?:lookup|query)',
       'ModelBackend.authenticate attempts lookup even when username or password is None. Return early for either missing credential before querying the user manager.',
       'In backends.py absent credentials must short-circuit authentication. If the username or the password is missing, skip the database lookup and return without authenticating.',
       'ModelBackend.authenticate fails because the password hashing algorithm is obsolete. Rehash every password with a larger iteration count before lookup.')
review('django__django-11477', 'RegexPattern.match',
       r'(?:named|keyword).{0,35}(?:group|argument)|groupdict~(?:filter|drop|omit|exclud|remov).{0,65}(?:none|null|unmatched)',
       'RegexPattern.match passes unmatched named groups as None keyword arguments. Filter out None values from groupdict so absent optional captures do not override downstream defaults.',
       'In resolvers.py omit unmatched entries from the named-group mapping. Dropping null keyword arguments lets optional route values use their normal defaults.',
       'RegexPattern.match must convert all captured numeric groups to integers; string types are the reason route defaults fail.')
review('django__django-11603', 'Avg|Sum',
       r'avg|average~sum~(?:allow|enable|support|accept|permit).{0,50}distinct|distinct.{0,50}(?:allow|enable|support|accept|permit)',
       'Avg and Sum inherit the distinct prohibition even though SQL supports it. Enable allow_distinct for both aggregate classes so their constructors accept DISTINCT.',
       'In aggregates.py permit distinct inputs for average and sum aggregation. Both classes need the capability flag enabled instead of rejecting the argument.',
       'Avg and Sum should deduplicate their final numeric results in Python after executing the ordinary aggregate query.')
review('django__django-12125', 'TypeSerializer',
       r'qualname|qualified.name|nested.{0,25}(?:path|name)~(?:use|preserv|retain|emit|serializ).{0,70}(?:qual|nest)|(?:qual|nest).{0,70}(?:use|preserv|retain|emit|serializ)',
       'TypeSerializer uses __name__, losing enclosing classes for nested types. Serialize the module plus __qualname__ so the emitted import path resolves the nested class.',
       'In serializer.py preserve the qualified name of non-builtin types. Emitting the nested class path rather than its short name keeps migration references importable.',
       'TypeSerializer should pickle every class object into migration files so importing its module is unnecessary.')
review('django__django-12262', 'parse_bits',
    r'keyword.only|kwonly~(?:declared|signature|complete|all).{0,60}(?:parameter|argument|list)|(?:parameter|argument|list).{0,60}(?:declared|signature|complete)',
    'parse_bits validates keywords against unhandled_kwargs, which excludes keyword-only parameters with defaults. Check the declared kwonly parameter list so those valid arguments are accepted.',
    'In library.py keyword-only acceptance must use the complete signature parameter list, not the remaining required arguments. Otherwise an optional keyword is reported as unexpected.',
    'parse_bits should discard all keyword-only arguments and let Python defaults supply their values unconditionally.',
    reject=r'discard.*all.*keyword.only|drop.*all.*kwonly')
review('django__django-12419', 'SECURE_REFERRER_POLICY',
       r'referrer~same.origin~default|unset|none',
       'SECURE_REFERRER_POLICY defaults to None, leaving the policy unset. Set the default to same-origin so referrer information is withheld for cross-origin requests.',
       'In global_settings.py choose same-origin as the default referrer policy. An unset value supplies no such policy to prevent referrer disclosure across origins.',
       'The referrer policy should default to unsafe-url so every destination receives the full source URL.')
review('django__django-12965', 'SQLDeleteCompiler.single_alias',
       r'(?:initial|base).{0,25}(?:alias|table)~(?:initializ|register|ensure|add).{0,70}(?:before|count)|(?:before|count).{0,70}(?:initializ|register|ensure|add)',
       'SQLDeleteCompiler.single_alias counts aliases before the base table is registered. Initialize the base alias with get_initial_alias before counting active table references.',
       'In compiler.py ensure the initial table alias exists before deciding whether deletion uses a single table. Counting an uninitialized alias map gives the wrong deletion strategy.',
       'SQLDeleteCompiler must delete joined tables first to satisfy foreign-key constraints; reverse the order of generated DELETE statements.')
review('django__django-13023', 'DecimalField.to_python',
       r'typeerror~valueerror~(?:catch|handle|convert|translat).{0,90}(?:validation|exception|error)',
       'DecimalField.to_python catches only InvalidOperation. Catch TypeError and ValueError from Decimal conversion too and translate them into the normal ValidationError for invalid input.',
       'In fields/__init__.py decimal coercion can raise TypeError or ValueError. Handle those exceptions with the existing invalid-value validation path instead of leaking them to callers.',
       'DecimalField.to_python should round every decimal to two places before validation to avoid unsupported precision.')
review('django__django-13109', 'ForeignKey.validate',
       r'base.manager|unfiltered.manager~(?:use|query|switch|validat).{0,80}(?:base.manager|unfiltered)|(?:base.manager|unfiltered).{0,80}(?:use|query|validat)',
       'ForeignKey.validate queries the default manager, which may hide valid related rows. Use the related model base manager for existence validation while retaining database routing and choice constraints.',
       'In related.py switch foreign-key validation to the unfiltered manager. A filtered default manager can otherwise make an existing referenced object appear absent.',
       'ForeignKey.validate should query every configured database and accept the reference if any database contains that primary key.')
review('django__django-13406', 'QuerySet.query',
       r'values.select|values.query|values projection~valuesiterable|dictionary.{0,25}iter|dict.{0,25}iter~(?:set|switch|select|restore|update).{0,65}(?:iter|valuesiterable)',
       'QuerySet.query replaces the query without updating its iterable class. When values_select is present, set ValuesIterable so the restored values query yields dictionaries rather than model objects.',
       'In query.py a values projection needs dictionary iteration after query assignment. Switch the iterable to ValuesIterable when the incoming query selects values.',
       'QuerySet.query should eagerly fetch every row during query assignment to preserve the previous result cache.')
review('django__django-13964', '_prepare_related_fields_for_save',
       r'empty.values|empty.string|empty.foreign.key~(?:copy|set|assign|propagat|refresh).{0,80}(?:pk|primary.key|related.{0,15}key)',
       '_prepare_related_fields_for_save refreshes a related key only when the stored value is None. Recognize all field.empty_values, including empty strings, and copy the now-saved related object primary key.',
       'In base.py an empty foreign key can be an empty string, not only null. Assign the related primary key after the referenced object has been saved for any field-defined empty value.',
       'The save preparation should always create a second related object so a missing foreign key receives a newly allocated identifier.')
review('django__django-14017', 'Q._combine',
       r'conditional|boolean.expression~(?:accept|allow|permit|support).{0,70}(?:conditional|expression)|(?:conditional|expression).{0,70}(?:accept|allow|permit|support)',
       'Q._combine rejects every operand that is not Q. Allow objects whose conditional attribute is True as well, so boolean expressions can participate in Q combinations.',
       'In query_utils.py accept conditional expressions alongside Q instances when combining predicates. The type guard currently excludes otherwise valid boolean operands.',
       'Q._combine should accept every arbitrary object and stringify it directly into SQL without checking whether it is a condition.')
review('django__django-14238', 'AutoFieldMeta.__subclasscheck__',
       r'issubclass|recursive.{0,25}subclass|indirect.{0,25}subclass~(?:replace|use|recogniz|check|accept).{0,75}(?:issubclass|descendant|subclass)',
       'AutoFieldMeta.__subclasscheck__ tests direct tuple membership, missing descendants of registered field classes. Use issubclass against the registered subclasses so indirect subclasses are recognized.',
       'In fields/__init__.py recognize descendants of the alternate auto field types. Replace exact-class membership with an issubclass check before the normal metaclass fallback.',
       'AutoFieldMeta should force every custom auto field to inherit directly from AutoField and forbid other inheritance chains.')
review('django__django-14534', 'BoundWidget.id_for_label',
    r'attrs|attribute|widget.id~(?:read|use|return|respect).{0,70}(?:id|identifier)|(?:id|identifier).{0,70}(?:read|use|return|respect)',
    'BoundWidget.id_for_label synthesizes an id from name and index, ignoring the actual widget id. Return the id from data attrs so labels target the rendered control.',
    'In boundfield.py use the widget attribute identifier for the label target. Reconstructing an id bypasses custom ids and can point the label at a nonexistent element.',
    'BoundWidget.id_for_label should use the choice display text as the HTML identifier because the option index is unstable.',
    reject=r'choice display text|display text.*html identifier')
review('django__django-14580', 'TypeSerializer',
       r'models.model~(?:add|include|emit|supply).{0,70}import|import.{0,70}(?:add|include|emit|supply)',
       'TypeSerializer emits models.Model with no supporting import. Include from django.db import models in that special case so the generated migration can resolve the base class.',
       'In serializer.py the models.Model special case needs an import of the models module. Supply that dependency with the serialized reference instead of leaving an undefined name.',
       'TypeSerializer should serialize the database table name instead of models.Model because migrations cannot refer to classes.')
review('django__django-14672', 'ManyToManyRel.identity',
       r'through.fields~(?:hashable|tuple).{0,65}(?:convert|make|normaliz)|(?:convert|make|normaliz).{0,65}(?:hashable|tuple)',
       'ManyToManyRel.identity includes through_fields directly, which may be an unhashable list. Normalize it with make_hashable before including it in the relation identity.',
       'In reverse_related.py convert the through-fields collection to a hashable representation. Relation hashing otherwise fails when the user supplies a list.',
       'ManyToManyRel.identity should omit through_fields entirely because all intermediate foreign-key arrangements are equivalent.')
review('django__django-14787', '_multi_decorate',
       r'wraps|metadata|__name__~(?:partial|bound.method)~(?:preserv|copy|apply|wrap|retain).{0,80}(?:metadata|method|partial|name)',
       '_multi_decorate hands decorators a partial bound method without the original metadata. Apply wraps(method) to the partial so decorators can access the method name and other attributes.',
       'In decorators.py preserve method metadata on the bound partial before invoking decorators. Copying those attributes avoids failures in decorators that inspect the wrapped callable.',
       'The decorator wrapper should invoke the unbound method without self so function decorators never see a bound callable.')
review('django__django-15098', 'language_code_prefix_re',
       r'language|locale~(?:two|2|three|3).{0,45}(?:suffix|part|component|segment)|(?:suffix|part|component|segment).{0,45}(?:two|2|three|3)',
       'language_code_prefix_re permits only one optional locale suffix. Allow up to two suffix components so three-part language codes are recognized in URL prefixes.',
       'In trans_real.py extend the language prefix regex to accept a second suffix segment. Locale codes with three parts currently fail prefix recognition.',
       'The language prefix parser should lowercase and remove every hyphen from the URL before looking up the language.')
review('django__django-15104', 'only_relation_agnostic_fields',
       r'\bto\b|target.key~(?:pop|safe|optional|missing|absent).{0,65}(?:key|remov|to|default)|(?:remov|key).{0,65}(?:safe|optional|missing|absent)',
       'only_relation_agnostic_fields deletes the to key unconditionally even when field deconstruction omits it. Use pop with a default to remove the optional relation target safely.',
       'In autodetector.py the deconstructed target key may be absent. Remove to with a safe default instead of raising KeyError while comparing relation-independent fields.',
       'The migration detector should add a to key naming the current model to every deconstructed field, including non-relational fields.')
review('django__django-15380', 'generate_renamed_fields',
       r'new|current|renamed~(?:model.name|model_name).{0,70}(?:lookup|state|index)|(?:lookup|state|index).{0,70}(?:model.name|model_name)',
       'generate_renamed_fields looks up the new state using old_model_name. Use the current model_name in to_state while retaining the old name for from_state when a model and its field are renamed.',
       'In autodetector.py index the destination state by the renamed model name. Looking up the old name in the new state fails during simultaneous model and field renames.',
       'generate_renamed_fields should suppress all field renames whenever the model is renamed and rebuild the table instead.')
review('django__django-15851', 'DatabaseClient.settings_to_cmd_args_env',
       r'parameter|option|argument~(?:before|preced|ahead).{0,65}(?:database|dbname)|(?:database|dbname).{0,65}(?:after|last|follow)',
       'The PostgreSQL client appends extra parameters after the database name. Put parameters before dbname so psql parses command options correctly rather than treating later arguments positionally.',
       'In client.py place user-supplied options ahead of the database argument. The database should follow the extra parameters in the generated psql command.',
       'The PostgreSQL client should quote the database password as a shell argument instead of passing it through the environment.')
review('django__django-15863', 'floatformat',
       r'\bstr\b|string~(?:repr|representation|quoted)~decimal|precision|coerc|convert',
       'floatformat feeds repr(text) into Decimal, introducing representation syntax for inputs such as Decimal objects. Use str(text) to preserve the numeric value without repr wrappers or a lossy float fallback.',
       'In defaultfilters.py convert the input through its numeric string before Decimal parsing. The repr representation can force fallback through floating point and lose precision.',
       'floatformat should convert all values to binary float immediately because Decimal cannot represent integral inputs reliably.')
review('django__django-15973', '_get_dependencies_for_foreign_key',
       r'through|intermediate~(?:resolve|use|dependenc).{0,70}(?:through|intermediate)|(?:through|intermediate).{0,70}(?:resolve|use|dependenc)',
       '_get_dependencies_for_foreign_key resolves the remote target again when computing a through-model dependency. Resolve field.remote_field.through instead to depend on the actual intermediate model.',
       'In autodetector.py the extra migration dependency must use the intermediate model. Resolving the relation target in the through branch adds the wrong dependency and misses the join table model.',
       'Foreign-key dependencies should always point to the current app initial migration, regardless of the referenced or intermediate model.')

review('django__django-15987', 'fixture_dirs',
       r'path|string~(?:convert|normaliz|str\().{0,70}(?:compar|director|path)|(?:compar|director|path).{0,70}(?:convert|normaliz|str\()',
       'fixture_dirs compares a string app directory with configured Path objects, missing duplicates. Convert configured directories to strings for the comparison so duplicate default fixture directories are rejected.',
       'In loaddata.py normalize directory paths to string values before comparing them. A Path and the same textual directory otherwise evade the duplicate fixture-directory check.',
       'The fixture loader should resolve duplicate directories by loading their fixtures twice, once for each configured path.')
review('django__django-16255', 'Sitemap.get_latest_lastmod',
       r'empty|no.items~max~default.{0,25}none|return.{0,25}none|none.{0,25}default',
       'Sitemap.get_latest_lastmod calls max on an empty item list, raising ValueError. Supply default=None to max so an empty sitemap has no latest modification time.',
       'In sitemaps/__init__.py max needs a None default when no items supply modification dates. The empty sequence should return None rather than raising an exception.',
       'Sitemap.get_latest_lastmod should use the current time whenever a sitemap item lacks a modification timestamp.')
review('django__django-16333', 'UserCreationForm.save',
       r'many.to.many|save.m2m~(?:after|following).{0,40}(?:user|save)|(?:call|persist|save).{0,65}(?:relation|save.m2m)',
       'UserCreationForm.save saves the user but omits many-to-many persistence. After saving on commit=True, call save_m2m when available so form-provided relationships are stored.',
       'In auth/forms.py persist many-to-many relations after the user save. The commit path currently stores the password and user row but never invokes the form relation-saving hook.',
       'UserCreationForm.save should call set_password twice because many-to-many fields require a second password hash to be saved.')
review('django__django-16485', 'floatformat',
       r'zero|\b0\b~(?:precision|decimal.places|\bp\b)~(?:integer|integral|fast.path|shortcut)',
       'floatformat excludes zero precision from its integral-value shortcut. Include p == 0 in the integer branch so integral inputs avoid the unnecessary Decimal quantization path.',
       'In defaultfilters.py the integer fast path should also handle zero decimal places. The strict negative-precision check sends that case into quantization unnecessarily.',
       'floatformat should increase Decimal precision globally for every template render, regardless of the requested number of places.')
review('django__django-16527', 'submit_row',
       r'save.as.new~add.permission|permission.{0,35}(?:add|creat)',
       'submit_row gates save-as-new on change permission even though the operation creates an object. Require add permission for that button while retaining the existing popup and change-view conditions.',
       'In admin_modify.py the save-as-new action needs permission to add a record. Checking only change permission exposes a creation action to users who cannot create objects.',
       'The admin save-as-new button should be visible to everyone because the existing object has already passed change permission checks.')
review('django__django-16642', 'FileResponse.set_headers',
    r'brotli|\bbr\b~compress~(?:mime|content.type|application/)',
    'FileResponse.set_headers lacks MIME overrides for br and compress encodings. Map them to application/x-brotli and application/x-compress so compressed downloads receive the correct content type.',
    'In response.py add Brotli and Unix compress to the compressed-file content-type mapping. Use their compression MIME types instead of the underlying uncompressed file type.',
    'FileResponse should decompress Brotli and compress files in memory before sending them so the original content type remains correct.',
    reject=r'decompress.{0,30}(?:brotli|compress).{0,30}memory')
review('django__django-16667', 'SelectDateWidget.value_from_datadict',
       r'overflow~(?:catch|handle|return).{0,70}(?:0.0.0|invalid|sentinel)|(?:invalid|sentinel).{0,70}(?:return|date)',
       'SelectDateWidget.value_from_datadict does not catch date-construction OverflowError for extreme inputs. Catch it and return the invalid date sentinel 0-0-0 so ordinary validation rejects the input.',
       'In widgets.py handle overflow while constructing a date by returning 0-0-0. Oversized components should become an invalid form value rather than crash extraction.',
       'SelectDateWidget should clamp any oversized year to the current year and accept the resulting date as valid.')
review('django__django-17087', 'FunctionTypeSerializer',
       r'class.method|bound.method~qualname|qualified.name|nested.class.path',
       'FunctionTypeSerializer serializes a bound class method using the class short name. Use the class __qualname__ so the enclosing class path is preserved for nested class methods.',
       'In serializer.py retain the qualified name of the owner of a class method. Its short name omits enclosing classes and produces an unresolvable migration reference.',
       'FunctionTypeSerializer should call the class method during serialization and store its return value in the migration.')
review('django__django-7530', 'makemigrations.Command.handle',
       r'app.config|get.app.config~(?:model|enumerat).{0,75}(?:app|scope)|(?:app|scope).{0,75}(?:model|enumerat)',
       'makemigrations passes an app label to the global get_models API, which does not select that app. Retrieve its AppConfig and enumerate that config models when checking migration routing.',
       'In makemigrations.py use get_app_config(app_label).get_models() to scope model enumeration. The registry-wide call treats the label as a different option and checks unrelated models.',
       'makemigrations should skip consistency checking for all apps with database routers because their migration histories cannot be inspected.')
review('pydata__xarray-6721', 'get_chunksizes',
       r'(?:_data|underlying|internal|private)~(?:avoid|without|trigger|eager|load|materializ).{0,60}(?:data|load|materializ)|(?:data|load|materializ).{0,60}(?:avoid|eager)',
       'get_chunksizes accesses v.data merely to test for chunks, which can load lazy data. Inspect v._data instead so chunk metadata queries do not materialize the array.',
       'In common.py test the underlying storage for chunk support without reading the public data property. That property can trigger eager loading during a metadata-only query.',
       'get_chunksizes should eagerly compute every array first so its chunk sizes can be calculated from concrete values.')
review('scikit-learn__scikit-learn-11578', '_log_reg_scoring_path',
       r'multi.class|multinomial~(?:pass|propagat|configur|set|preserv).{0,75}(?:scor|regression|multi.class)|(?:scor|regression).{0,75}(?:pass|propagat|configur|set|preserv)',
       '_log_reg_scoring_path constructs its scoring LogisticRegression without multi_class. Pass the trained multiclass mode into that estimator so scoring interprets the fitted coefficients consistently.',
       'In logistic.py configure the scoring estimator with the same multiclass strategy used for fitting. Otherwise multinomial coefficients are evaluated using a different prediction mode.',
       'The logistic regression scoring path should reverse class labels before computing accuracy because sorted labels invert binary predictions.')
review('scikit-learn__scikit-learn-13135', 'KBinsDiscretizer.fit',
       r'cent(?:er|re)~sort|order~before.{0,55}(?:edge|midpoint)|(?:edge|midpoint).{0,55}(?:calcul|comput|deriv)',
       'KBinsDiscretizer assumes fitted k-means centers remain ordered. Sort the centers before computing adjacent midpoints so bin edges are monotonically ordered.',
       'In _discretization.py sort cluster centres before deriving bin edges. Ordered initialization does not guarantee the fitted centres stay ordered, so unsorted midpoints define invalid bins.',
       'KBinsDiscretizer should double the number of k-means clusters to prevent bins from being empty.')
review('sphinx-doc__sphinx-9258', 'PyTypedField.make_xrefs',
       r'union|pipe|vertical.bar~(?:split|delimit|separat).{0,75}(?:\||union|pipe|type)|(?:\||union|pipe).{0,75}(?:split|delimit|separat)',
       'PyTypedField.make_xrefs does not split pipe-separated union types. Add the vertical bar with surrounding whitespace to its delimiters so each union member gets its own cross-reference.',
       'In python.py recognize the pipe as a type separator when building field references. Split union members individually instead of treating the whole union as one target.',
       'The Python domain should remove all union members except the first because only one type can be linked per parameter.')
review('sympy__sympy-19040', 'dmp_ext_factor',
       r'norm~(?:original|full|unsquared|multiplicit).{0,75}(?:polynomial|input|\bf\b)|(?:polynomial|input).{0,75}(?:original|full|multiplicit)',
       'dmp_ext_factor computes the square-free norm from the already square-free part. Pass the original polynomial F to dmp_sqf_norm so the norm computation retains the information in the full input.',
       'In factortools.py use the original input polynomial for the norm step. Feeding only its square-free reduction discards multiplicity information needed by extension-field factorization.',
       'dmp_ext_factor should discard the leading coefficient before all factorization steps because extension fields cannot represent it.')
review('sympy__sympy-20428', 'Expression.__bool__',
       r'is.zero|zero.predicate|mathematical.zero~(?:structural|!=|inequality|float|symbolic|zero)',
       'Expression.__bool__ compares structurally with integer zero, so other mathematically zero expressions can be truthy. Use the expression is_zero predicate rather than structural inequality.',
       'In expressiondomain.py determine truth from mathematical zero knowledge. Negating is_zero recognizes zeros that are structurally different from the integer literal.',
       'Expression.__bool__ should return False for every symbolic expression because an unknown symbolic value can never be nonzero.')
review('sympy__sympy-21612', 'StrPrinter._print_Mul',
       r'power|\bpow\b~parenthes|group~denominator|division|reciprocal',
       'StrPrinter._print_Mul only marks multiplicative bases for denominator parentheses. Include power bases too so a reciprocal of a nested power is grouped unambiguously in string output.',
       'In str.py parenthesize power expressions in denominators as well as products. Without grouping, printed division of nested powers can represent a different expression.',
       'StrPrinter should numerically evaluate all nested exponents before printing so parentheses are never needed.')
review('sympy__sympy-22714', 'Point.__new__',
       r'imaginary|im\(~(?:definit|known|explicit).{0,45}(?:nonzero|non.zero|false)|is.zero.{0,25}false',
       'Point.__new__ truth-tests imaginary parts and may reject an undecidable value. Reject a numeric coordinate only when its imaginary part is definitively nonzero, using is_zero is False.',
       'In point.py require known nonzero imaginary content before rejecting a coordinate. An unknown zero status must not be treated as proof that the point is complex.',
       'Point.__new__ should discard every imaginary part automatically and keep only the real component of each coordinate.')
review('sympy__sympy-22914', 'PythonCodePrinter|_known_functions',
       r'\bmin\b~\bmax\b~builtin|built.in|map',
       'The Python code printer lacks Min and Max mappings. Map them to the built-in min and max functions so generated Python uses supported callable names.',
       'In pycode.py register both minimum and maximum functions as Python builtins, min and max. Missing mappings leave these symbolic operations unsupported in emitted code.',
       'The Python printer should replace Min and Max with sorting the arguments at code-generation time, even when they contain symbols.')
review('sympy__sympy-23534', 'symbols',
       r'\bcls\b|custom.class|requested.class~(?:pass|forward|propagat|preserv).{0,70}(?:recurs|element|class)|(?:recurs|element).{0,70}(?:pass|forward|propagat|class)',
       'symbols drops cls when recursively processing an iterable of names. Forward the requested class to each recursive call so collection elements use the caller-selected symbol type.',
       'In symbol.py preserve the custom class across recursive symbol creation. Each name in an iterable must receive cls instead of silently reverting to the default Symbol class.',
       'symbols should always convert iterable inputs to a plain list because tuple containers cannot hold custom symbol subclasses.')
review('sympy__sympy-23950', 'Contains.as_set',
    r'(?:return|use|expos).{0,60}(?:set|second.argument)|(?:set|second.argument).{0,60}(?:return|use|expos)~membership|contains|args\[1\]|contained',
    'Contains.as_set raises NotImplementedError despite already storing the membership set. Return the second argument, the set in the Contains expression, to implement conversion.',
    'In contains.py expose the set operand of membership when converting to a set. Returning args[1] supplies the existing set instead of reporting an unsupported operation.',
    'Contains.as_set should return a singleton containing the tested element, regardless of the set used by the membership predicate.',
    reject=r'singleton.*tested element|return.*singleton')
review('sympy__sympy-24213', 'UnitSystem._collect_factor_and_dimension',
       r'equivalent|equivalence~dimension.system|equivalent.dims~add|sum|addend',
       'UnitSystem._collect_factor_and_dimension compares addend dimensions structurally. Ask the dimension system whether they are equivalent so sums of dimensionally equivalent expressions are accepted.',
       'In unitsystem.py validate sums using equivalent_dims from the active dimension system. Different dimension expressions may represent the same physical dimension and should be addable.',
       'UnitSystem should ignore all dimensional mismatches in sums and simply add the numeric factors.')
review('django__django-11555', 'find_ordering_name',
       r'orderby|ordering.expression~(?:append|preserv|retain|accept|direct).{0,65}(?:expression|orderby|item)|(?:expression|orderby).{0,65}(?:append|preserv|retain|accept|direct)',
       'find_ordering_name recursively treats every related-model ordering item as a field name. Preserve OrderBy expressions directly in the result instead of passing them into string name resolution.',
       'In compiler.py accept an OrderBy item from related-model ordering as an expression. Append it directly and skip the field-name recursion that cannot handle expression objects.',
       'find_ordering_name should stringify each OrderBy expression and look for a database column with that exact name.')
review('django__django-11951', '_batched_insert',
       r'batch~(?:min|cap|limit|clamp).{0,65}(?:backend|database|maximum|batch)|(?:backend|database).{0,65}(?:min|cap|limit|clamp)',
       '_batched_insert lets an explicit batch_size bypass the backend limit. Cap the requested size at the database maximum, with a minimum supported maximum of one, before splitting inserts.',
       'In query.py clamp user-specified insertion batches to the backend bulk limit. Otherwise a large requested batch exceeds the database parameter capacity.',
       '_batched_insert should retry oversized batches indefinitely without changing their size because database limits are temporary.')
review('django__django-12273', 'Model._set_pk_val',
    r'parent~(?:set|assign|propagat|synchroniz).{0,70}(?:key|pk|field)|(?:key|pk|field).{0,70}(?:set|assign|propagat|synchroniz)',
    'Model._set_pk_val changes only the local primary key, leaving inherited parent key fields stale. Propagate the assigned value to parent-link target fields as well as the local pk.',
    'In base.py synchronize parent primary-key attributes when assigning pk on an inherited model. Updating only the child key leaves inconsistent identifiers across the inheritance links.',
    'Model._set_pk_val should allocate unrelated primary keys for every parent model so inherited rows can be stored independently.',
    reject=r'allocate.{0,20}primary key|unrelated primary key')
review('django__django-12663', 'Query.output_field',
       r'target~(?:prefer|use|return|select).{0,75}(?:target|concrete|referenced)|(?:target|concrete|referenced).{0,75}(?:prefer|use|return|type)',
       'Query.output_field uses a selected column field, which can be a relation rather than its concrete target. Prefer select.target when available and fall back to select.field to expose the actual output type.',
       'In query.py infer a single selected column from its target field first. The referenced concrete field carries the scalar type that the relation field alone does not provide.',
       'Query.output_field should always return a text field because all SQL subqueries are implicitly converted to strings.')
review('django__django-13012', 'ExpressionWrapper.get_group_by_cols',
    r'group.by~(?:delegat|forward|use).{0,75}(?:wrapped|inner|expression)|(?:wrapped|inner).{0,75}(?:delegat|group)',
    'ExpressionWrapper.get_group_by_cols does not delegate grouping to its wrapped expression. Forward get_group_by_cols and its alias to the inner expression so grouping follows that expression semantics.',
    'In expressions.py derive group-by columns from the wrapped expression. Delegating the grouping method avoids treating the wrapper itself as an extra grouping term.',
    'ExpressionWrapper should force every wrapped constant into GROUP BY regardless of the inner expression grouping requirements.',
    reject=r'force.*wrapped constant.*group|force.*constant.*into group')
review('django__django-13158', 'Query.clone|Query.set_empty',
       r'combin|union~clon|cop(y|ies|ied)~(?:empty|none).{0,75}(?:propagat|child|component|subquer)|(?:propagat|child|component|subquer).{0,75}empty',
       'Query.set_empty leaves combined subqueries populated, and clone shares those queries. Clone every combined component and propagate set_empty into them so none() empties a combination without mutating its source.',
       'In query.py emptying a union must mark each component empty. Copy the combined subqueries during cloning first, so this propagation does not empty queries shared by other querysets.',
       'Query.set_empty should remove only the first branch of a union, retaining later branches so their cached results remain available.')
review('django__django-13569', 'Random.get_group_by_cols',
       r'random~group.by~empty|\[\]|no.columns|exclude|omit',
       'Random inherits grouping behavior that adds a nondeterministic expression to GROUP BY. Override get_group_by_cols to return an empty list so random ordering does not split aggregate groups.',
       'In math.py exclude the random function from group-by columns. Returning no grouping expressions prevents each randomly evaluated row from creating a separate group.',
       'Random should use a fixed seed for every SQL row so GROUP BY can safely include the random function.')
review('django__django-14089', 'OrderedSet.__reversed__',
       r'revers~(?:dict|backing|underlying).{0,70}(?:iter|order|revers)|(?:iter|order|revers).{0,70}(?:dict|backing|underlying)',
       'OrderedSet lacks reverse iteration support. Implement __reversed__ by reversing the backing dictionary so elements are yielded in reverse insertion order.',
       'In datastructures.py delegate reverse iteration to the underlying ordered dict. This adds reversed() support while preserving the reverse of the set insertion order.',
       'OrderedSet should sort elements by value when reversed is called, since insertion order is irrelevant to set iteration.')
review('django__django-14349', 'URLValidator.__call__',
       r'tab|\\t~newline|carriage|\\n|\\r~reject|raise|unsafe|invalid',
       'URLValidator can accept URLs containing tab, carriage-return or newline characters that URL parsing strips. Reject these unsafe characters explicitly before scheme and URL validation.',
       'In validators.py disallow embedded tabs and line breaks before parsing a URL. The parser may silently remove them, so validate the original input for these unsafe characters.',
       'URLValidator should strip all control characters and accept the resulting URL, since browser normalization makes the original input safe.')
review('django__django-14915', 'ModelChoiceIteratorValue.__hash__',
    r'hash~(?:underlying|wrapped|self.value|stored).{0,65}(?:value|hash)|(?:hash).{0,65}(?:value)',
    'ModelChoiceIteratorValue defines equality but has no hash implementation. Hash its underlying value so it can be used as a dictionary key consistently with equality.',
    'In forms/models.py make choice wrapper values hashable by delegating to the wrapped value hash. That preserves equality semantics for set membership and dictionary lookup.',
    'ModelChoiceIteratorValue should hash the model object identity, even when two wrappers compare equal by their stored value.',
    reject=r'hash.*model object identity|model object identity')
review('django__django-15277', 'CharField.__init__',
       r'max.length|length.limit~(?:none|unset|absent|unspecified)~(?:skip|only|guard|avoid|unless|conditional)',
       'CharField.__init__ always installs MaxLengthValidator, even when max_length is None. Add it only for a specified length so unbounded fields do not run an invalid length comparison.',
       'In fields/__init__.py skip the maximum-length validator when the length limit is unset. A None limit must not be compared with the input string length.',
       'CharField should replace an unspecified maximum length with zero and reject every nonempty string.')
review('django__django-15695', 'RenameIndex.database_forwards',
       r'(?:same|unchanged|equal|identical).{0,35}(?:name|index)|(?:name|index).{0,35}(?:same|unchanged|equal|identical)~(?:return|skip|no.op|avoid)',
       'RenameIndex.database_forwards alters an index even when its existing name equals new_name. Return early for identical names so the operation is a no-op instead of dropping or recreating the same index.',
       'In operations/models.py skip the index rename when the old and requested names are equal. An unchanged index name needs no schema alteration.',
       'RenameIndex should append a random suffix whenever old and new names match so every migration creates a new physical index.')
review('pytest-dev__pytest-7205', '_show_fixture_action',
       r'saferepr|safe.repr|safe.representation~param~limit|42|truncat|bounded|short',
       '_show_fixture_action formats cached parameters directly, allowing failing or huge representations to disrupt setup display. Use saferepr with maxsize=42 for safe, bounded parameter output.',
       'In setuponly.py show fixture parameters through a length-limited safe representation. saferepr both handles broken repr methods and truncates excessive output.',
       'The setup display should execute each callable parameter before printing it, so only its computed value is shown.')
review('sphinx-doc__sphinx-8551', 'PyTypedField.make_xref|DocFieldTransformer.transform',
    r'module~class~(?:context|environment|\benv\b)~pass|propagat|attach|copy|forward',
    'Python typed-field references lack module and class context. Pass the build environment from docfield transformation and attach its current Python module and class to the generated cross-reference.',
    'In python.py and docfields.py propagate the environment to field cross-reference creation. Copy module and class context onto the reference so relative type names resolve in their defining scope.',
    'Typed-field references should always resolve from the root module and discard enclosing class context to avoid ambiguous names.',
    reject=r'resolve from the root module|discard.*class context')
review('sympy__sympy-23824', 'kahane_simplify',
       r'free~(?:preserv|retain|original).{0,50}order|order.{0,50}(?:preserv|retain)|prepend.{0,50}(?:slice|prefix)',
       'kahane_simplify inserts each leading free index at position zero, reversing their order. Prepend the entire free-index prefix in its original order to each resulting index sequence.',
       'In gamma_matrices.py preserve the order of free gamma matrices before the first contraction. Concatenate that prefix as a block instead of repeatedly inserting at the front.',
       'kahane_simplify should sort all free indices alphabetically because gamma matrices commute with each other.')
review('django__django-11099', 'ASCIIUsernameValidator|UnicodeUsernameValidator',
       r'newline|line.break~(?:end.of.string|\\z|strict.end|absolute.end)',
       'Both username validators use dollar end anchors, which can match before a final newline. Use the strict end-of-string anchor \\Z so a trailing newline is rejected.',
       'In auth/validators.py require the absolute end of the string for ASCII and Unicode usernames. The existing anchor accepts a final line break outside the allowed character class.',
       'Username validation should strip a trailing newline and silently accept the normalized username instead of rejecting the supplied input.')
review('django__django-11211', 'UUIDField.get_prep_value',
    r'uuid~(?:prep|prepar).{0,70}(?:to.python|convert|normaliz)|(?:to.python|convert|normaliz).{0,70}(?:prep|prepar)',
    'UUIDField lacks Python normalization in get_prep_value. Run the parent preparation and then to_python so prepared values are UUID objects before comparisons and database preparation.',
    'In fields/__init__.py normalize UUID inputs during value preparation. Calling to_python after the superclass converts textual identifiers into the same UUID representation used internally.',
    'UUIDField should generate a new random UUID for every prepared value, replacing user-provided identifiers.',
    reject=r'generate.*new random uuid')
review('django__django-11790', 'AuthenticationForm.__init__',
       r'maxlength|max.length~widget|html~(?:set|synchroniz|propagat|update|copy)',
       'AuthenticationForm updates the username field max_length but leaves the widget maxlength stale. Set the HTML widget attribute to the same model-derived length, including the existing fallback.',
       'In auth/forms.py synchronize the username widget maxlength with the form field limit. Updating only server-side metadata leaves browser input constrained by the old length.',
       'AuthenticationForm should remove server-side username length validation because the browser already enforces the input limit.')
review('django__django-12143', '_get_edited_object_pks',
       r'prefix~escap|literal~regex|regular.expression|pattern',
       '_get_edited_object_pks interpolates the formset prefix into a regex without escaping it. Apply re.escape to the prefix so special characters are matched literally when extracting submitted keys.',
       'In admin/options.py escape regex metacharacters in the formset prefix before compiling the primary-key pattern. A literal prefix must not change the pattern matching rules.',
       'The admin should reject every formset prefix containing punctuation instead of supporting literal special characters.')
review('django__django-12193', 'CheckboxInput.get_context',
       r'checked~(?:copy|new|fresh).{0,50}(?:attrs|mapping|dict)|(?:attrs|mapping|dict).{0,50}(?:copy|new|fresh)',
       'CheckboxInput.get_context mutates the caller attrs dictionary when adding checked. Create a fresh mapping with the checked entry so shared attributes do not make later checkboxes appear selected.',
       'In widgets.py copy the attribute mapping before setting checked. Reusing and mutating the input dictionary leaks selection state between checkbox renders.',
       'CheckboxInput.get_context should set checked to True for every non-null input, even an explicit False value.')
review('django__django-12858', 'Model._check_ordering',
       r'lookup~transform~(?:accept|allow|check|recogniz).{0,75}(?:lookup|both)|(?:lookup|both).{0,75}(?:accept|allow|check|recogniz)',
       'Model._check_ordering recognizes transforms but not lookups at the end of an ordering path. Check get_lookup as well and report a missing field only when neither lookup nor transform exists.',
       'In base.py accept registered lookups alongside transforms during ordering checks. A valid lookup suffix is currently misreported as a nonexistent field.',
       'Model._check_ordering should suppress every unknown-field error because database backends can resolve arbitrary ordering names.')
review('django__django-13363', 'TruncDate.as_sql|TruncTime.as_sql',
       r'time.?zone|tzinfo|tzname~(?:get.tzname|explicit|supplied|expression).{0,75}(?:zone|tz|use|honor)|(?:use|honor|respect).{0,75}(?:get.tzname|explicit|supplied)',
       'TruncDate and TruncTime hardcode the current timezone, ignoring the expression tzinfo. Use get_tzname for both casts so an explicit timezone is honored with the existing timezone configuration rules.',
       'In datetime.py resolve the zone through the truncation expression helper. Calling get_tzname preserves a supplied timezone instead of always casting in the current zone.',
       'TruncDate and TruncTime should disable timezone conversion entirely and always cast timestamps as UTC.')
review('django__django-13516', 'OutputWrapper.flush',
    r'flush~(?:delegat|forward|call).{0,60}(?:underlying|wrapped|_out|stream)|(?:underlying|wrapped|stream).{0,60}(?:delegat|forward|flush)',
    'OutputWrapper does not forward flush through its inherited stream method. Implement flush explicitly and delegate to the wrapped output when that method is available.',
    'In management/base.py flushing the wrapper must flush the underlying stream. Add a guarded forwarding method so buffered command output is actually emitted.',
    'OutputWrapper.flush should close the underlying stream immediately so all subsequent writes are prevented.',
    reject=r'close.*stream immediately|close.*underlying stream immediately')
review('django__django-13670', 'DateFormat.y',
       r'(?:two|2).{0,15}digit~(?:zero.pad|leading.zero|02d)|(?:pad).{0,25}zero~modulo|% ?100|last.two|year',
       'DateFormat.y slices the year string, failing for years with fewer than four digits. Take the year modulo 100 and zero-pad to two digits for the two-digit year format.',
       'In dateformat.py compute the last two year digits arithmetically and include leading zeros. String slicing does not produce a two-digit value for early years.',
       'DateFormat.y should prepend the current century to every year below one hundred before formatting it.')
review('django__django-14373', 'DateFormat.Y',
       r'(?:four|4).{0,15}digit~zero.pad|leading.zero|04d|pad.{0,25}zero',
       'DateFormat.Y returns the raw year number, omitting leading zeros for early dates. Format the year with four-digit zero padding so the full-year token has the expected width.',
       'In dateformat.py emit a four-digit year string including leading zeros. Returning an integer loses the padding needed for dates before year one thousand.',
       'DateFormat.Y should add 1900 to all years below one thousand to make the output contain four digits.')
review('django__django-15022', 'ModelAdmin.get_search_results',
       r'(?:single|one|combined).{0,35}filter|filter.{0,35}(?:single|one|combined)~(?:term|search).{0,75}(?:combin|collect|\band\b)|(?:combin|collect).{0,75}(?:term|search)',
       'ModelAdmin.get_search_results filters once per search term, creating separate relation joins. Collect each term OR predicate and combine them in a single AND filter so terms constrain the same related join.',
       'In admin/options.py combine the per-term search conditions before one filter call. Chaining filters can use different related rows and unnecessarily multiply joins.',
       'The admin search should OR all search words together so a record matching any single word is always returned.')
review('django__django-15368', 'QuerySet.bulk_update',
       r'resolve.expression~(?:attribute|protocol|hasattr|duck|check).{0,65}(?:expression|resolve)|(?:expression|resolve).{0,65}(?:attribute|protocol|hasattr|duck|check)',
       'QuerySet.bulk_update recognizes only Expression subclasses and wraps other expression-like objects in Value. Check for resolve_expression instead so F and other protocol-compatible expressions are preserved.',
       'In query.py detect bulk-update expressions by the resolve_expression protocol. A concrete-class test misses valid expression objects and incorrectly treats them as literal values.',
       'QuerySet.bulk_update should convert F expressions to their string representation before wrapping them in Value.')
review('django__django-15375', 'Aggregate.resolve_expression',
       r'coalesce~is.summary|summary.flag|summariz~(?:copy|propagat|preserv|set|retain)',
       'Aggregate.resolve_expression wraps a default in Coalesce but loses is_summary. Copy the aggregate summary flag to the wrapper so later query compilation treats it as a summary expression.',
       'In aggregates.py preserve summarization metadata when adding Coalesce for a default. The wrapper needs the same is_summary flag as its resolved aggregate.',
       'Aggregate.resolve_expression should discard the default whenever an aggregate is used in a summary query.')
review('django__django-15467', 'formfield_for_foreignkey',
       r'empty.label|blank.label~(?:preserv|respect|retain|custom|supplied).{0,70}(?:label|value)|(?:label|value).{0,70}(?:preserv|respect|retain|custom|supplied)',
       'formfield_for_foreignkey overwrites a supplied empty_label for blank radio fields. Preserve the keyword value and use the translated None label only as a fallback.',
       'In admin/options.py respect the custom blank label for foreign-key radio controls. The default label should apply only when no empty_label was supplied.',
       'The admin should always remove the blank choice from radio fields even when the model field permits blank values.')
review('django__django-15525', 'build_instance',
       r'natural.key~(?:state.db|database|\bdb\b)~(?:before|prior).{0,60}(?:natural|key|call)|(?:set|assign).{0,60}(?:state|database)',
       'build_instance invokes natural_key on a temporary object whose database state is unset. Assign obj._state.db before calling natural_key so related lookups use the deserialization database.',
       'In serializers/base.py set the temporary instance database before computing its natural key. Otherwise relations accessed by that method can query the default database.',
       'build_instance should search all databases for a matching natural key and merge the matching rows into one object.')
review('django__django-15572', 'get_template_directories',
       r'empty|falsy|blank~(?:skip|filter|exclud|ignore).{0,70}(?:director|path)|(?:director|path).{0,70}(?:skip|filter|exclud|ignore)',
       'get_template_directories turns empty directory entries into the current working directory. Filter empty paths from both engine directories and loader directories before registering template watches.',
       'In autoreload.py skip blank template directories from both configuration sources. Treating an empty path as cwd creates an unintended broad template watch.',
       'The template autoreloader should watch the entire working directory recursively for every configured template engine.')
review('django__django-15731', 'BaseManager._get_queryset_methods',
       r'wraps|__wrapped__~signature|metadata|introspect',
       'BaseManager._get_queryset_methods copies only name and doc onto forwarding methods. Use functools.wraps so __wrapped__ and the rest of the metadata support correct signature introspection.',
       'In manager.py decorate generated forwarding methods with wraps. Preserving the wrapped-method metadata lets inspection recover the original queryset method signature.',
       'Generated manager methods should erase their signatures entirely and reject keyword arguments to match a generic forwarding API.')
review('django__django-16139', 'UserChangeForm.__init__',
       r'password~(?:primary.key|\bpk\b|instance.{0,15}id|user.{0,15}id)~(?:link|url|path).{0,75}(?:include|construct|use)|(?:include|construct|use).{0,75}(?:link|url|path)',
       'UserChangeForm builds the password link relative to the current path, which fails outside the normal change view. Include the instance primary key in the relative password URL to target the actual user.',
       'In auth/forms.py construct the password-change link with the user id and the appropriate parent path. The fixed relative link can otherwise resolve against the wrong page.',
       'UserChangeForm should link to the logged-in administrator password page instead of the user being edited.')
review('django__django-16569', 'BaseFormSet.add_fields',
       r'index~none|empty.form~(?:guard|check|before|only).{0,75}(?:compar|index|count)|(?:compar).{0,75}(?:guard|none)',
       'BaseFormSet.add_fields compares index with the initial form count even when the empty form has index=None. Guard against None before that comparison when deciding whether to add deletion controls.',
       'In formsets.py the empty form has no numeric index. Check for a non-None index before comparing it to the initial count, avoiding a TypeError with extra-form deletion disabled.',
       'BaseFormSet.add_fields should always enable deletion on extra forms, ignoring can_delete_extra to avoid special cases.')
review('django__django-16612', 'AdminSite.catch_all_view',
       r'query.string|query.parameter|full.path~(?:preserv|retain|keep|use|get.full.path)',
       'AdminSite.catch_all_view appends a slash to request.path and loses the query string. Use get_full_path(force_append_slash=True) so the redirect preserves query parameters.',
       'In sites.py retain the query string when redirecting to a slash-terminated admin URL. Construct the destination from the full request path rather than the path alone.',
       'AdminSite.catch_all_view should permanently drop query parameters from every redirect to prevent duplicate URLs.')
review('django__django-9296', 'Paginator.__iter__',
    r'iter|yield~(?:page.objects|self.page|page\(page|each.page|pages).{0,65}(?:range|yield|iter)|(?:range|yield|iter).{0,65}(?:page.objects|self.page|each.page|pages)',
    'Paginator lacks iteration over its pages. Implement __iter__ to walk page_range and yield self.page(page_number), exposing Page objects rather than raw page numbers.',
    'In paginator.py make the paginator iterable by yielding each page from the page-number range. Call the existing page accessor so iteration returns actual page objects.',
    'Paginator iteration should yield individual records directly and bypass page boundaries altogether.',
    reject=r'yield individual record|bypass page boundary')
review('matplotlib__matplotlib-22719', 'StrCategoryConverter.convert|UnitData.update',
       r'empty|size|nonempty~(?:warn|log|diagnostic).{0,75}(?:skip|guard|suppress|only)|(?:skip|guard|suppress|only).{0,75}(?:warn|log|diagnostic)',
       'Categorical conversion treats all(empty) as numeric and logs convertibility for empty data. Guard both diagnostics with nonzero array size so empty inputs produce neither the numeric deprecation warning nor the conversion log.',
       'In category.py only warn about numeric passthrough or log convertible strings for nonempty inputs. Empty arrays vacuously satisfy those tests and trigger misleading diagnostics.',
       'The category converter should raise an error for every empty sequence because a category mapping must always contain at least one value.')
review('psf__requests-1766', 'HTTPDigestAuth.build_digest_header',
       r'qop~auth~quot|double.quot',
       'HTTPDigestAuth.build_digest_header emits qop=auth without quotes. Quote the auth value in the Digest authorization header so servers expecting the quoted quality-of-protection token accept it.',
       'In auth.py serialize the Digest qop value as a quoted auth string. The unquoted form can fail server parsing even though the computed digest is otherwise valid.',
       'Digest authentication should remove qop entirely and use the older digest algorithm regardless of the server challenge.')
review('psf__requests-2317', 'Session.request',
       r'method~native.string|to.native.string|decode~bytes|byte|representation',
       'Session.request uses builtin_str on the HTTP method, which turns bytes into their representation on Python 3. Use to_native_string to decode byte methods into the native method text.',
       'In sessions.py normalize the method using the native-string helper. Byte input must become the method name, not a string containing the bytes representation.',
       'Session.request should convert every HTTP method to GET because byte-valued methods are not supported by HTTP.')
review('scikit-learn__scikit-learn-14710', '_check_early_stopping_scorer',
    r'classes|original.label|class.label~(?:map|decode|convert|restor).{0,75}(?:label|target|class)|(?:label|target).{0,75}(?:map|decode|convert|restor)~train|validation|both|scoring.dataset',
    'The early-stopping scorer receives encoded class indices while predictions use original labels. Map training and validation targets back through classes_ before calling the scorer for classifiers.',
    'In gradient_boosting.py restore original class labels for both scoring datasets. Comparing classifier predictions with integer-coded targets produces incorrect early-stopping scores.',
    'Early stopping should score only the first training sample because validation labels are unreliable after binning.',
    reject=r'score only.*first training sample')
review('scikit-learn__scikit-learn-25747', '_wrap_in_pandas_container',
       r'dataframe~index~(?:preserv|retain|keep|ignore|not.overwrite)',
       '_wrap_in_pandas_container overwrites an existing DataFrame index with the input index. Preserve the DataFrame own index and ignore the supplied index in that branch, while still allowing column names to change.',
       'In _set_output.py keep the index of an already-produced DataFrame. Replacing it with the original input index breaks transformers that intentionally change rows or their labels.',
       'The pandas wrapper should reset every output index to a fresh RangeIndex so all transformers produce identical row labels.')
review('sphinx-doc__sphinx-8475', 'HyperlinkAvailabilityChecker.check',
    r'toomanyredirects|redirect.{0,20}(?:limit|loop)~head~(?:retry|fallback|fall.back).{0,45}get|(?:get).{0,45}(?:retry|fallback)',
    'The link checker retries HEAD failures with GET only for HTTPError. Handle TooManyRedirects in the same branch so HEAD redirect loops can fall back to a successful GET request.',
    'In linkcheck.py a redirect-limit exception from HEAD should trigger the GET retry. Some servers loop only for HEAD, so that exception must share the existing fallback.',
    'The link checker should treat every redirect loop as a valid link without issuing any further request.',
    reject=r'treat.*redirect loop.*valid link|valid link without.*request')
review('sympy__sympy-13372', 'evalf',
       r'(?:real|imaginary|component)~(?:nonnumeric|non.numeric|symbolic|not.numeric)~notimplementederror|unsupported|raise',
       'evalf leaves precision variables unset when a real or imaginary component is nonnumeric. Raise NotImplementedError for those components rather than constructing a partial numerical result.',
       'In evalf.py explicitly reject symbolic real or imaginary parts in the numeric fallback. Reporting an unsupported evaluation avoids using uninitialized precision information.',
       'evalf should silently replace every nonnumeric real or imaginary component with zero to complete numerical evaluation.')
review('sympy__sympy-13551', 'Product._eval_product',
       r'exp|exponential~sum~log~product',
       'Product._eval_product incorrectly distributes a product over additive pieces when numerator decomposition makes no progress. Represent the remaining product as exp(Sum(log(p))) instead of summing products of terms.',
       'In products.py a product of a sum cannot be evaluated by adding separate products. Preserve the whole factor with an exponential of the sum of its logarithms in that fallback.',
       'Product._eval_product should distribute multiplication over the summation by adding independent products for each additive term.')
review('sympy__sympy-17655', 'Point.__rmul__',
       r'reflected|reverse|left.scalar|scalar.on.the.left|rmul~(?:delegat|call|reuse|reusing|forward).{0,60}(?:mul|multipli|multiplication)',
       'Point implements multiplication only when the point is the left operand. Add __rmul__ delegating to __mul__ so a scalar on the left scales the point coordinates too.',
       'In point.py support reflected scalar multiplication by reusing the existing multiplication implementation. Left-scalar expressions should produce the same scaled coordinates.',
       'Point.__rmul__ should return the dot product of the scalar with the point coordinates instead of another point.')
review('sympy__sympy-19637', 'kernS',
       r'kern|placeholder~(?:hit|flag).{0,60}(?:false|initializ|set)|(?:false|initializ|set).{0,60}(?:hit|flag)',
       'kernS tests for a placeholder even on a branch that never creates kern. Set hit=False when no placeholder substitution occurs and perform membership testing only after the placeholder exists.',
       'In sympify.py initialize the replacement flag to false when no kernel marker was generated. Checking marker membership outside its creation branch can reference an undefined variable.',
       'kernS should replace every parenthesis with a random marker before parsing, regardless of whether multiplication protection is needed.')
review('sympy__sympy-20801', 'Float.__eq__',
       r'zero~(?:boolean|type.check)~(?:before|after|defer|move|order)',
       'Float.__eq__ handles zero via general truthiness before checking Boolean operands. Move the zero fallback after the type-specific comparisons so floating zero does not compare equal to Boolean false.',
       'In numbers.py perform Boolean and numeric type checks before the zero truthiness shortcut. Deferring that fallback prevents false equality between a floating zero and a logical value.',
       'Float.__eq__ should consider every falsy Python or symbolic value equal to floating zero, regardless of its type.')
review('sympy__sympy-20916', 'split_super_sub',
       r'unicode|non.ascii~digit|subscript~regex|pattern|letter',
       'split_super_sub recognizes digit suffixes only after ASCII letters. Use a Unicode-aware letter pattern and digit class so non-ASCII symbol names receive trailing-digit subscripts too.',
       'In conventions.py extend the name-plus-digits regex to Unicode letters. A Greek or other non-ASCII name followed by digits should split into a base name and numeric subscript.',
       'split_super_sub should transliterate every Unicode symbol name to ASCII before rendering it, discarding the original letters.')
review('sympy__sympy-21847', 'itermonomials',
       r'(?:sum|total).{0,35}(?:degree|exponent|power)|(?:degree|exponent|power).{0,35}(?:sum|total)~min|lower.bound~max|individual',
       'itermonomials tests the maximum individual exponent against min_degree. Compare the sum of exponents, the total degree, in both commutative and noncommutative branches so mixed monomials are not omitted.',
       'In monomials.py the lower bound applies to total degree rather than the largest variable power. Sum the exponent counts before filtering by minimum degree.',
       'itermonomials should require every individual variable exponent to exceed the minimum degree, even when the total degree already meets it.')

review('sympy__sympy-23262', '_recursive_to_string',
       r'tuple~(?:trailing|final).{0,20}comma|comma.{0,30}(?:tuple|singleton)~single|one.element|one.item',
       '_recursive_to_string emits tuples without a trailing comma, so a one-element tuple becomes a parenthesized scalar. Add the trailing comma to tuple output to preserve its container type.',
       'In lambdify.py keep singleton tuple syntax when generating expressions. A final comma before the closing parenthesis distinguishes a one-item tuple from an ordinary grouped value.',
       'The lambdify printer should convert every tuple into a list because generated Python cannot represent singleton tuples.')
review('django__django-11740', 'generate_altered_fields',
       r'alter~foreign.key|related.model~(?:add|collect|attach|include|pass).{0,65}dependenc|dependenc.{0,65}(?:add|collect|attach|include|pass)',
       'generate_altered_fields creates AlterField without dependencies for a new foreign-key target. Collect the relation dependencies and attach them to the operation so the referenced model exists before the alteration.',
       'In autodetector.py include foreign-key dependencies when generating an altered field. The migration planner otherwise may schedule the alteration before creating its related model.',
       'The migration detector should remove all cross-app dependencies from AlterField to avoid cycles, even when the target model is new.')
review('django__django-12308', 'display_for_field',
       r'json~get.prep.value|field.{0,35}(?:serializ|prepar)|(?:serializ|prepar).{0,35}field~typeerror|fallback|fall.back',
       'display_for_field uses generic Python display for JSONField values. Serialize through field.get_prep_value for JSON output, falling back to generic display if serialization raises TypeError.',
       'In admin/utils.py render JSON using the field preparation method rather than a Python repr. Preserve a fallback for TypeError so values the JSON encoder cannot handle still display.',
       'display_for_field should evaluate strings as Python expressions before displaying JSON, converting any executable text into a value.')
review('django__django-13028', 'Query.check_filterable',
       r'resolve.expression|expression.protocol~(?:only|guard|check|require).{0,65}(?:expression|filterable)|(?:filterable).{0,65}(?:only|expression)',
       'Query.check_filterable treats any object filterable attribute as expression metadata. Require resolve_expression before enforcing filterable=False so ordinary model values with a same-named field are not rejected.',
       'In query.py apply the filterable restriction only to objects implementing the expression protocol. An unrelated value attribute must not control whether a filter is allowed.',
       'Query.check_filterable should ignore filterable=False for all SQL expressions because every expression is valid in a WHERE clause.')
review('django__django-13297', '_wrap_url_kwargs_with_deprecation_warning',
       r'lazy~(?:type|typed|original).{0,60}(?:value|proxy|wrap)|(?:value|proxy|wrap).{0,60}(?:type|typed|original)~simplelazyobject|deprecat|warning',
       'The URL-kwargs deprecation wrapper uses SimpleLazyObject, which does not preserve the value type protocol adequately. Use lazy(access_value, type(value)) so deferred access retains type-specific behavior while issuing the warning.',
       'In generic/base.py wrap deprecated URL context values with a typed lazy proxy. The warning should remain deferred while operations use the original value type rather than a generic SimpleLazyObject.',
       'The deprecation wrapper should convert all URL keyword values into strings immediately and emit warnings during view construction.')
review('django__django-13512', 'display_for_field|JSONField.prepare_value',
       r'json~ensure.ascii.{0,15}false|(?:preserv|unescaped|literal).{0,45}(?:unicode|non.ascii)|(?:unicode|non.ascii).{0,45}(?:preserv|unescaped|literal)~encoder|serializ|dumps',
       'JSON display escapes non-ASCII characters by default. Use json.dumps with ensure_ascii=False and the configured encoder in both admin display and form preparation so Unicode remains readable.',
       'In admin/utils.py and forms/fields.py preserve literal Unicode when serializing JSON for display. Disable ASCII escaping while retaining each field encoder.',
       'JSON display should strip all non-ASCII characters before encoding so the output remains valid JSON.')
review('django__django-13568', 'check_user_model',
       r'username~unique.constraint|uniqueness.constraint|uniqueconstraint~single|only|field|unconditional',
       'check_user_model recognizes only the username field unique flag. Accept an unconditional single-field UniqueConstraint on USERNAME_FIELD as equivalent uniqueness enforcement.',
       'In auth/checks.py a username may be uniquely constrained at model level. Check total_unique_constraints for exactly that one field before issuing the nonunique-username diagnostic.',
       'The user-model check should accept any multi-column unique constraint containing the username because that makes the username alone unique.')
review('django__django-14315', 'BaseDatabaseClient.runshell',
       r'env|environment~(?:empty|none)~inherit|parent|os.environ',
       'The database shell passes an empty env mapping when no overrides exist, clearing the inherited environment. Pass None in that case and merge nonempty overrides with os.environ.',
       'In client.py preserve the parent environment when no database-specific variables are supplied. An empty mapping replaces the environment; None inherits it, while explicit overrides should be merged.',
       'The database shell should always launch with an empty environment to prevent system PATH settings from affecting the client.')
review('django__django-14404', 'AdminSite.catch_all_view',
       r'path.info|script.prefix|mount.prefix~request.path|full.path~redirect',
       'AdminSite.catch_all_view redirects using path_info, omitting the deployment script prefix. Keep path_info for URL resolution but build the redirect from request.path so mounted admin URLs retain their prefix.',
       'In sites.py resolve the slash variant without the script prefix, then redirect with the full request path. Using path_info for the destination drops the mount prefix.',
       'The admin catch-all should remove the deployment prefix from every redirect so the client always reaches a root-mounted URL.')
review('django__django-14500', 'MigrationExecutor.unapply_migration',
       r'replac|squash~(?:record|mark|unappl).{0,75}(?:both|itself|replacement|squash)|(?:both|itself|replacement|squash).{0,75}(?:record|mark|unappl)',
       'unapply_migration records only replaced migrations when reverting a squash. Also record the replacement migration itself as unapplied so neither it nor its replaced entries remain applied.',
       'In executor.py mark both the squashed migration and the individual migrations unapplied. The replacement branch currently skips clearing the squash own recorder entry.',
       'unapply_migration should leave the squashed migration marked applied permanently because its individual migrations already track reversals.')
review('django__django-14539', 'urlize.trim_punctuation',
       r'punctuation~(?:count|length|number)~(?:end|suffix|negative|tail)|entity',
       'trim_punctuation uses an unescaped string length as an index into encoded text. Compute the trailing punctuation count and slice that many characters from the end, preserving entity-encoded URL content.',
       'In html.py transfer only the suffix count of punctuation from the URL to the trail. Absolute offsets from unescaped text are wrong when entities change the string length.',
       'urlize should fully unescape and discard all HTML entities before identifying a link, even when they belong inside its URL.')
review('django__django-14771', 'get_child_arguments',
       r'(?:\bx\b|xoptions|interpreter).{0,25}option|\-x~(?:propagat|forward|preserv|pass).{0,75}(?:child|reload|option)|(?:child|reload).{0,75}(?:propagat|forward|preserv|option)',
       'get_child_arguments preserves warnings but drops CPython -X options. Forward sys._xoptions as flags or key=value arguments so the autoreloaded child uses the same interpreter configuration.',
       'In autoreload.py propagate CPython interpreter options to the restarted child. Reconstruct each -X setting, including options with values, instead of silently losing them on reload.',
       'The autoreloader should discard all interpreter flags so each child always starts with Python default settings.')
review('django__django-15280', 'get_prefetch_queryset',
       r'cached|cache~(?:not|unless|only|skip|preserv).{0,70}(?:cached|cache|assign|overwrite)|(?:cached|cache).{0,70}(?:preserv|overwrite|skip)',
       'Reverse prefetch unconditionally assigns the related object back-reference, overwriting a relation already cached by the queryset. Assign it only when field.is_cached is false so existing related-object state is preserved.',
       'In related_descriptors.py preserve a cached forward relation during reverse prefetch. Skip back-reference assignment when the field cache already holds the related object.',
       'Reverse prefetch should clear every related-object cache before assignment so all queryset annotations are discarded consistently.')
review('django__django-15930', 'When.as_sql',
       r'empty|blank~(?:true|always.true|tautolog).{0,65}(?:predicat|condition|value)|(?:predicat|condition).{0,65}(?:true|tautolog)',
       'When.as_sql can receive an empty condition for a filter matching everything. Compile Value(True) in that case so CASE WHEN has an explicit true predicate instead of invalid empty SQL.',
       'In expressions.py replace an empty WHEN condition with an always-true predicate. Empty SQL can mean no restriction in WHERE, but CASE syntax still requires a condition.',
       'When.as_sql should omit the entire branch whenever its condition compiles to an empty string, even if that condition matches every row.')
review('django__django-16493', 'FileField.deconstruct',
       r'storage~callable~(?:before|preserv|retain|serializ|compare)',
       'FileField.deconstruct compares the evaluated storage with default_storage before considering its callable. Select _storage_callable first and compare that object so a callable returning default storage is still serialized.',
       'In files.py preserve a supplied storage callable during deconstruction even if its current result is the default storage. Compare the original callable before deciding to omit the storage argument.',
       'FileField.deconstruct should call the storage factory repeatedly until it returns a nondefault storage instance to serialize.')
review('django__django-16662', 'MigrationWriter.as_string',
       r'import~(?:group|before|first).{0,65}(?:from|plain|import)|(?:from).{0,65}(?:after|group)~sort|order',
       'MigrationWriter sorts imports only by module name, mixing import and from statements. Sort first by statement kind, placing plain imports before from imports, then by module.',
       'In writer.py group plain import statements ahead of from-import statements. Module-name sorting should occur within those groups for consistent migration import order.',
       'MigrationWriter should reverse alphabetic module order and put every from import before all plain imports.')
review('django__django-16819', 'AddIndex.reduce',
       r'(?:addindex|add.index|index.creation|creation.*index)~(?:removeindex|remove.index|index.removal|removal.*index)~cancel|empty|eliminat|reduc.{0,30}\[\]',
       'AddIndex lacks a reduction for a subsequent RemoveIndex with the same name. Cancel the matching creation/removal pair by returning an empty operation list during migration optimization.',
       'In operations/models.py eliminate index creation followed by removal of that same index. The optimizer should reduce the pair to no operations rather than retain unnecessary schema work.',
       'AddIndex.reduce should cancel every later RemoveIndex regardless of its index name.',
       reject=r'cancel every later removeindex|regardless of its index name')
review('django__django-16899', '_check_readonly_fields_item',
       r'readonly|read.only~(?:include|name|identify|show).{0,65}(?:field|invalid|offend)|(?:field|invalid|offend).{0,65}(?:include|name|identify|show)~message|error|diagnostic',
       'The readonly-fields check reports the option position but omits the invalid field value. Include field_name in the error message so users can identify the offending readonly-field reference.',
       'In admin/checks.py name the actual invalid readonly field in the diagnostic. Reporting only the configuration label makes the bad reference unnecessarily hard to locate.',
       'The readonly-fields check should automatically remove unknown names from the admin configuration instead of reporting an error.')
review('psf__requests-1724', 'Session.request',
       r'method~builtin.str|builtin.string|built.in.string|native.str~(?:coerc|convert|normaliz).{0,60}(?:method|string)|(?:method).{0,60}(?:coerc|convert|normaliz)',
       'Session.request forwards the supplied method type unchanged into request construction. Coerce it with builtin_str before uppercasing so the method uses the runtime built-in string type.',
       'In sessions.py normalize the HTTP method to a built-in string before constructing the Request. Passing another text type unchanged can break downstream method handling.',
       'Session.request should preserve arbitrary method objects and pass them directly to the socket without conversion or uppercasing.')
review('scikit-learn__scikit-learn-13124', 'StratifiedKFold._make_test_folds',
       r'check.random.state|shared.{0,25}(?:rng|generator)|randomstate~seed|shuffle|class',
       'StratifiedKFold passes a raw seed into per-class splitting, restarting random sequences. Normalize it once with check_random_state and share the resulting generator so class shuffles advance one RNG state.',
       'In _split.py construct a shared RandomState before assigning folds. Reusing the integer seed for each class resets shuffling instead of drawing successive permutations.',
       'StratifiedKFold should reseed the random generator with the same integer before shuffling each class so all classes use identical permutations.')
review('sympy__sympy-15345', 'MCodePrinter._print_MinMaxBase',
       r'min~max~mathematica|square.bracket|function.printer~dispatch|map|route|register',
       'The Mathematica printer lacks Min/Max dispatch through its function printer. Register both names and route MinMaxBase to _print_Function so it emits Mathematica function-call syntax.',
       'In mathematica.py map minimum and maximum to Min and Max and dispatch their common base through the function formatter. That produces square-bracket calls rather than unsupported output.',
       'The Mathematica printer should render Min and Max as Python lowercase builtins with parentheses.')
review('sympy__sympy-16766', 'PythonCodePrinter._print_Indexed',
       r'index|indices~(?:bracket|subscript)~(?:print|render|comma|base)',
       'PythonCodePrinter lacks an Indexed handler. Print the base followed by square brackets containing recursively printed, comma-separated indices so indexed expressions become valid Python subscripts.',
       'In pycode.py render indexed objects as base[index] or multi-index bracket syntax. Each index must use the printer recursively rather than leaving the symbolic Indexed object unsupported.',
       'PythonCodePrinter should replace every indexed expression with the base object and discard all index arguments.')
review('sympy__sympy-20590', 'Printable',
       r'empty.slots|__slots__.{0,15}\(\)|slots.{0,20}empty~dict|instance.dictionary|subclass',
       'Printable is a mixin without slots, so slotted subclasses still inherit an instance dictionary. Declare empty __slots__ on the mixin to avoid introducing __dict__.',
       'In _print_helpers.py give Printable empty slots. Otherwise inheriting this printing mixin defeats a subclass slot layout by adding per-instance dictionary storage.',
       'Printable should add every subclass attribute to a shared class dictionary so individual instances never need their own state.')
review('django__django-11095', 'BaseModelAdmin.get_inlines|ModelAdmin.get_inline_instances',
       r'get.inlines|inline.{0,25}hook~request|object|dynamic~(?:call|use|route|delegat)',
       'get_inline_instances reads self.inlines directly, preventing request-specific selection. Add get_inlines(request, obj) and call that hook so subclasses can choose inline classes dynamically.',
       'In admin/options.py route inline construction through an overridable hook receiving the request and object. The default can return self.inlines while custom admins vary the selection.',
       'The admin should instantiate every registered inline for every model and hide unwanted ones only with CSS.')
review('django__django-12276', 'FileInput.use_required_attribute',
       r'fileinput|file.input~initial|existing.file~(?:required).{0,60}(?:suppress|omit|not|disable)|(?:suppress|omit|not|disable).{0,60}required',
       'Only ClearableFileInput suppresses the required attribute for an initial file. Move that behavior into FileInput so ordinary file inputs also avoid requiring a replacement upload when a file already exists.',
       'In widgets.py omit HTML required on a file input with an existing initial value. The rule belongs on FileInput and can then be inherited by its clearable subclass.',
       'FileInput should always emit required, even when the form already has a stored file that the user wants to keep.')
review('django__django-13112', 'ForeignObject.deconstruct',
       r'app.label~(?:preserv|retain|case|unchanged)~(?:lowercase|lower.case|lower).{0,45}(?:model|name)|(?:model).{0,45}(?:lowercase|lower.case|lower)',
       'ForeignObject.deconstruct lowercases the entire string relation target, altering the app label. Split app and model and lowercase only the model name, preserving app-label case.',
       'In related.py retain the app label exactly when serializing a dotted relation string. Only the model-name component should be normalized to lowercase.',
       'ForeignObject.deconstruct should uppercase both the app label and model name so all relation targets share a canonical form.')
review('django__django-13590', 'Query.resolve_lookup_value',
       r'namedtuple~(?:unpack|positional|\*values|splat)',
       'resolve_lookup_value rebuilds every tuple type from one iterable, which is invalid for namedtuple constructors. Detect namedtuples and unpack resolved values as positional arguments.',
       'In query.py reconstruct a namedtuple by passing its resolved fields separately. The ordinary tuple constructor convention of one iterable does not work for this tuple subclass.',
       'resolve_lookup_value should flatten every namedtuple into a plain string before using it in a lookup.')
review('django__django-13786', 'CreateModel.reduce',
       r'alter.model.options|alterable|option.keys~(?:remov|drop|clear|discard).{0,75}(?:absent|missing|omit|option)|(?:absent|missing|omit).{0,75}(?:remov|drop|clear|discard)',
       'CreateModel.reduce merges AlterModelOptions without removing omitted options. Remove alterable option keys absent from the new options so optimization preserves option deletion semantics.',
       'In operations/models.py clear old alterable options that an AlterModelOptions operation omits. A simple dictionary merge incorrectly retains options that the migration removed.',
       'CreateModel.reduce should retain every old option forever and ignore attempts to remove model options during optimization.')
review('django__django-13794', 'lazy.__proxy__.__add__|lazy.__proxy__.__radd__',
    r'lazy|proxy~(?:add|concatenat)~(?:cast|evaluat|unwrap).{0,60}(?:value|operand|proxy)|(?:value|operand|proxy).{0,60}(?:cast|evaluat|unwrap)',
    'The lazy proxy lacks explicit addition methods that evaluate the wrapped value. Add __add__ and __radd__ using __cast so both operand orders delegate to the resolved value.',
    'In functional.py implement forward and reflected addition for lazy values. Unwrap the proxy before adding so concatenation uses the underlying value in either position.',
    'The lazy proxy should concatenate its repr text without evaluating the wrapped value, exposing the proxy object identity in the result.',
    reject=r'concatenate.*repr|repr text without evaluat')
review('django__django-13810', 'BaseHandler.load_middleware',
       r'adapt~(?:middleware.not.used|skipped|unused)~(?:only|after|preserv|retain|temporary|commit)',
       'load_middleware replaces the handler with an adapted version before middleware construction can raise MiddlewareNotUsed. Keep adaptation temporary and commit it only after construction succeeds so skipped middleware cannot change the handler mode.',
       'In handlers/base.py preserve the existing handler when unused middleware is skipped. Assign the adapted handler only after successful initialization, keeping async-mode tracking consistent.',
       'load_middleware should keep every adaptation even when middleware is unused and force all later middleware into synchronous mode.')

review('django__django-13821', 'check_sqlite_version',
       r'sqlite~3\.9(?:\.0)?~minimum|require|reject|threshold',
       'check_sqlite_version still accepts SQLite 3.8.3 although the supported minimum is now 3.9.0. Raise the version threshold and update the error message to enforce that requirement.',
       'In sqlite3/base.py require SQLite version 3.9 or later. Both the minimum-version comparison and its diagnostic must reflect the new supported baseline.',
       'The SQLite version check should accept all older versions and silently disable database integrity constraints when features are unavailable.')
review('django__django-13933', 'ModelChoiceField.to_python',
       r'invalid.choice|validation.error~(?:params|parameter|interpolat)~value',
       'ModelChoiceField.to_python raises invalid_choice without the rejected value parameter. Include params containing value so customized validation messages can interpolate the submitted value.',
       'In forms/models.py supply the failing value when constructing the choice-validation error. The message interpolation context is otherwise missing its value entry.',
       'ModelChoiceField.to_python should replace every invalid choice with the first queryset object rather than raising a validation error.')
review('django__django-14559', 'QuerySet.bulk_update',
       r'rows|row.count|affected~(?:sum|accumulat|total).{0,70}(?:update|count|batch)|(?:update|count|batch).{0,70}(?:sum|accumulat|total)~return',
       'QuerySet.bulk_update discards each update row count and returns None. Accumulate affected rows across batches, return the total, and return zero for an empty input.',
       'In query.py report the total number of rows updated by bulk_update. Sum the batch update results and use zero when there are no objects to process.',
       'QuerySet.bulk_update should return the number of supplied objects without considering whether the database actually matched those rows.')
review('django__django-14855', 'AdminReadonlyField.get_admin_url',
       r'current.app|admin.site|site.namespace~(?:pass|supply|use|select|respect).{0,70}(?:site|namespace|reverse)|(?:site|namespace).{0,70}(?:pass|supply|reverse)',
       'get_admin_url reverses a related-object link without the active admin-site namespace. Pass current_app=self.model_admin.admin_site.name so readonly links target the correct admin site.',
       'In helpers.py supply the current admin site when reversing the related-object URL. Otherwise multiple admin registrations can make the readonly link point into the wrong site.',
       'get_admin_url should hardcode the default admin namespace regardless of the site displaying the readonly field.')
review('django__django-15161', 'Func|Value|ExpressionWrapper|When|Case|OrderBy',
       r'deconstruct|serializ~django.db.models~(?:public|path|decorator)',
       'Several expression classes deconstruct using internal module paths. Add explicit deconstructible paths under django.db.models for Func, Value, ExpressionWrapper, When, Case and OrderBy so migrations use their public imports.',
       'In expressions.py pin the public serialization paths of the standard expression classes. Deconstruction should emit django.db.models references instead of exposing their implementation module.',
       'Expression classes should be serialized as their current SQL strings, removing all Python import references from migrations.')
review('django__django-15315', 'Field.__hash__',
       r'creation.counter~(?:stable|chang|model|bind|attach)~hash',
       'Field.__hash__ includes model metadata that changes when a field is attached to a model. Hash only creation_counter so the field hash stays stable across model binding.',
       'In fields/__init__.py use the immutable creation counter for hashing. Including app and model names changes a field hash after binding and breaks dictionary or set lookup.',
       'Field.__hash__ should generate a fresh random number on every call to prevent collisions between model fields.')
review('django__django-16595', 'AlterField.reduce',
       r'alter.field~(?:same.field|successive|consecutive|later|last)~(?:reduc|retain|keep|replace|collapse)',
       'AlterField.reduce handles later removal but not another alteration of the same field. Reduce successive AlterField operations to the later operation so intermediate field definitions are discarded.',
       'In operations/fields.py collapse consecutive alterations of one field to the final definition. The later AlterField supersedes the earlier one just as removal does.',
       'AlterField.reduce should merge alterations of unrelated fields into one operation using whichever field name sorts first.')
review('django__django-17084', 'Query.get_aggregation',
       r'window|over.clause~(?:subquery|outer.query|inner.query)~(?:detect|wrap|push|force|reference)',
       'get_aggregation detects referenced subqueries but misses window annotations. Detect contains_over_clause on referenced annotations and force subquery wrapping so aggregation happens outside the window computation.',
       'In query.py an aggregate over a window result needs an outer query. Track references to window annotations and push those calculations into a subquery before aggregating.',
       'get_aggregation should inline every window expression directly inside the aggregate function in the same SELECT clause.')
review('pydata__xarray-4356', '_maybe_null_out',
       r'min.count|minimum.count~(?:product|prod|multiply).{0,60}(?:dimension|shape|size|axis)|(?:dimension|shape|size|axis).{0,60}(?:product|prod|multiply)',
       '_maybe_null_out rejects multiple reduction axes and assumes one dimension size. Use the product of reduced-axis sizes to compute valid counts, allowing min_count across multiple dimensions.',
       'In nanops.py calculate reduction capacity by multiplying the selected dimension lengths. Subtract missing counts from that product so minimum-count masking works for a tuple of axes.',
       '_maybe_null_out should use the largest reduced dimension length as the total count and ignore the other reduction axes.')
review('scikit-learn__scikit-learn-13439', 'Pipeline.__len__',
    r'len|length~(?:steps|step.count|number.of.steps)',
    'Pipeline supports indexing but does not implement length. Add __len__ returning len(self.steps) so callers can query the number of configured pipeline steps.',
    'In pipeline.py expose the step count through the Python length protocol. The length should be the number of entries in steps, including configured passthrough entries.',
    'Pipeline.__len__ should return the number of samples seen during fit rather than the number of configured steps.',
    reject=r'return.*number of sample|samples seen during fit')
review('sympy__sympy-21930', 'AntiSymmetricTensor._latex|CreateBoson._latex|CreateFermion._latex',
       r'latex~(?:brace|group).{0,60}(?:whole|entire|expression|operator)|(?:whole|entire|expression|operator).{0,60}(?:brace|group)~superscript|power|dagger|exponent',
       'Second-quantization LaTeX outputs already contain superscripts but lack outer grouping. Brace the entire tensor or creation-operator expression so a later power does not create a double superscript.',
       'In secondquant.py group the complete LaTeX operator in braces before it can receive another exponent. Existing dagger or tensor superscripts otherwise conflict with an outer power.',
       'The second-quantization printer should remove dagger superscripts entirely, rendering creation and annihilation operators identically.')
review('astropy__astropy-13236', '_convert_data_to_col',
       r'structured|record.array~(?:remove|stop|avoid|no.longer).{0,70}(?:mixin|view|coerc|convert)|(?:mixin|view).{0,70}(?:remove|stop|avoid)',
       '_convert_data_to_col automatically views structured ndarrays as NdarrayMixin. Remove that special coercion so structured arrays use ordinary column conversion unless already a genuine mixin.',
       'In table.py stop converting every structured array into a mixin view. The normal column path should handle these arrays instead of forcing NdarrayMixin behavior.',
       'Structured arrays should always be flattened into a single unstructured numeric array before being added to a table.')
review('django__django-10999', 'standard_duration_re',
       r'sign~(?:whole|entire|single|shared).{0,55}(?:time|duration|component)|(?:time|duration).{0,55}(?:whole|entire|single|shared)',
       'standard_duration_re assigns independent minus signs to time components, misinterpreting negative durations. Capture one shared sign for the whole time portion and parse unsigned hours, minutes and seconds.',
       'In dateparse.py parse a single leading sign for the time part of a duration. Applying that sign to all time components avoids treating only the first component as negative.',
       'The duration parser should take the absolute value of each negative component and return a positive duration in every case.')
review('django__django-11433', 'construct_instance',
       r'cleaned.data|cleaned.value~(?:empty|nonempty|non.empty)~default|omitted',
       'construct_instance preserves a model default whenever input was omitted, even if cleaning supplied a value. Preserve the default only when the cleaned value is also empty so custom cleaned data is saved.',
       'In forms/models.py check cleaned_data before skipping an omitted field with a default. A nonempty value supplied by cleaning must override the default rather than be discarded.',
       'construct_instance should always preserve model defaults and ignore cleaned values for any field missing from raw POST data.')
review('django__django-11815', 'EnumSerializer',
       r'enum~(?:name|member).{0,65}(?:lookup|serializ|index|reference)|(?:lookup|serializ|index|reference).{0,65}(?:name|member)~value',
       'EnumSerializer reconstructs members from their values, which can vary or be unsuitable for serialization. Emit an enum lookup by member name instead of serializing and calling the value constructor.',
       'In serializer.py reference the enum member by its stable name. A name-indexed lookup avoids embedding a translated or otherwise changing enum value in migrations.',
       'EnumSerializer should serialize only the numeric position of each member in declaration order and use that ordinal forever.')
review('django__django-12708', 'alter_index_together',
       r'non.unique|unique.{0,15}false~(?:index|constraint)~(?:filter|select|delete|remov|restrict)',
       'alter_index_together searches for any matching index, including unique indexes on the same columns. Add unique=False to the deletion lookup so only the intended nonunique composite index is removed.',
       'In schema.py restrict index-together removal to non-unique indexes. A coincident unique constraint must not be selected as the ordinary index being deleted.',
       'alter_index_together should delete every index on the affected columns, including unique constraints, before rebuilding the schema.')
review('django__django-13033', 'find_ordering_name',
       r'attname|attribute.name~(?:last|final).{0,35}(?:component|piece|segment)|pieces\[.1\]~order',
       'find_ordering_name compares a related field attname with the entire lookup path. Compare it with the final path component so explicit foreign-key attribute ordering does not expand the related model default ordering.',
       'In compiler.py check the last lookup segment against the field attribute name. A qualified foreign-key id path should order by that id, not inherit the related model ordering.',
       'find_ordering_name should always expand related-model default ordering even when the user explicitly requested a foreign-key id.')
review('django__django-13417', 'QuerySet.ordered',
       r'group.by|grouped~default.order|model.order~(?:ignore|exclude|not|false|disregard)',
       'QuerySet.ordered reports model default ordering even for grouped queries that ignore it. Exclude default ordering when group_by is set while retaining explicit ordering checks.',
       'In query.py a grouped queryset is not ordered merely because its model declares default ordering. Disregard that default for GROUP BY queries when reporting ordered status.',
       'QuerySet.ordered should always return True for grouped queries because grouping guarantees sorted output.')
review('django__django-13658', 'ManagementUtility.execute',
       r'prog|program.name~(?:pass|set|supply|explicit).{0,65}(?:parser|prog|name)|(?:parser|prog).{0,65}(?:pass|set|supply|explicit)',
       'ManagementUtility.execute constructs its preliminary parser without the utility program name. Pass prog=self.prog_name so parsing diagnostics use the supplied command name rather than ambient argv.',
       'In management/__init__.py give the option parser the explicit program name. Inferring it from process arguments can be wrong when management commands are invoked programmatically.',
       'ManagementUtility.execute should overwrite sys.argv globally before constructing every parser, changing the process arguments for all callers.')
review('django__django-14608', 'BaseFormSet.full_clean',
       r'nonform|non.form~(?:css|error.class|class).{0,70}(?:set|supply|mark|pass|initializ)|(?:set|supply|mark|pass|initializ).{0,70}(?:css|error.class|class)',
       'BaseFormSet.full_clean constructs non-form errors without their distinguishing CSS class. Pass error_class=nonform both initially and when replacing the list after ValidationError.',
       'In formsets.py mark cross-form errors with the nonform class. Supply that class for both empty error-list creation and the validation-exception path so rendering distinguishes them.',
       'BaseFormSet.full_clean should attach every cross-form error to the first form field instead of maintaining a non-form error list.')
review('django__django-14765', 'ProjectState.__init__',
       r'real.apps~(?:none|empty)~(?:preserv|retain|same|identity|assert|set)',
       'ProjectState.__init__ replaces an explicitly empty real_apps set because it tests truthiness. Default only for None, require a supplied set, and retain that same set even when empty.',
       'In state.py distinguish absent real_apps from an empty set. Preserve the supplied set object and assert its type instead of coercing or replacing it based on truthiness.',
       'ProjectState.__init__ should always copy real_apps into a fresh list so external updates can never be reflected in the state.')
review('django__django-14792', '_get_timezone_name',
       r'tzname~(?:fixed|offset)~(?:fallback|fall.back|or.str|string)',
       '_get_timezone_name always uses str(timezone), which can obscure a fixed offset name. Prefer timezone.tzname(None) and fall back to str only when no name is returned.',
       'In timezone.py obtain the fixed-offset zone name through tzname first. Keep the string representation as a fallback for zones whose name depends on a date.',
       '_get_timezone_name should use the current local timezone name for every timezone object, ignoring its own offset.')
review('django__django-16901', 'WhereNode.as_sql',
       r'xor~odd|parity|modulo.{0,15}2|mod.{0,15}2~sum|count|operand',
       'WhereNode emulates XOR by requiring exactly one true operand, which is wrong for longer chains. Apply modulo two to the summed truth values so any odd number of true operands passes.',
       'In where.py n-ary XOR needs odd parity, not a count equal to one. For more than two operands, reduce the truth-count sum modulo 2 before testing it.',
       'WhereNode should treat XOR as true whenever at least one operand is true, regardless of how many others are true.')
review('matplotlib__matplotlib-20676', 'SpanSelector._setup_edge_handle',
       r'(?:axis|axes).{0,25}(?:bound|limit)|get.xbound|get.ybound~(?:initial|handle).{0,70}(?:position|bound)|(?:position|bound).{0,70}(?:initial|handle)',
       'SpanSelector initializes edge handles from selector extents, which can change axis bounds. Initialize their positions from the current x or y bounds according to direction so construction preserves the view.',
       'In widgets.py place new span handles at existing axis limits. Using selector extents during setup can autoscale the axes unexpectedly; use the bounds for the selected orientation.',
       'SpanSelector should reset both axes to zero-to-one bounds whenever edge handles are created.',
       reject=r'reset both axes|zero.to.one bounds|zero to one')
review('matplotlib__matplotlib-26342', 'Collection.set_paths',
    r'(?:base|collection).{0,35}(?:set.paths|setter)|set.paths~(?:assign|store|set).{0,35}paths~stale',
    'Collection.set_paths raises NotImplementedError while PathCollection alone implements it. Move the setter into the base collection, storing paths and marking the artist stale so other collections support updates.',
    'In collections.py implement the base path setter by assigning _paths and setting stale=True. All collection subclasses should inherit the update behavior previously restricted to PathCollection.',
    'Collection.set_paths should silently discard new paths and clear the stale flag to prevent redraws.',
    reject=r'silently discard|discard.*new path')
review('pytest-dev__pytest-10051', 'LogCaptureHandler.clear|LogCaptureFixture.clear',
       r'(?:clear|empt).{0,35}(?:existing|captured|same|in.place).{0,35}(?:record|list)|(?:record|list).{0,35}(?:clear|empt)~stream|text|buffer',
       'caplog.clear uses reset, replacing the record list and leaving stored references stale. Add a handler clear that empties records in place and resets the text stream, then delegate caplog.clear to it.',
       'In logging.py clear the existing captured-record list instead of assigning a new list. Reset the stream too, so caplog clearing updates shared record references and captured text together.',
       'caplog.clear should replace the handler object entirely and leave previously stored record lists untouched.')
review('sphinx-doc__sphinx-7454', '_parse_annotation',
       r'none~(?:obj|object|object.role|object.reference).{0,35}(?:role|reference|type)|(?:role|reference|type).{0,35}(?:obj|object)~class',
       '_parse_annotation gives None a class cross-reference, but None is an object. Use the obj role for None while retaining class references for other annotation names.',
       'In python.py link a None annotation as an object rather than a class. The class-only reference type cannot resolve this singleton correctly.',
       'The annotation parser should replace None with the string NoneType and require users to document a custom class with that name.')
review('sphinx-doc__sphinx-9281', 'object_description',
    r'enum~(?:class|type).{0,40}(?:member|name)|(?:member|name).{0,40}(?:class|type)~repr|display|render|format',
    'object_description uses generic repr for enum members, including implementation-style value details. Format enum defaults as ClassName.member_name for a concise usable reference.',
    'In inspect.py render an enum member using its class and member names. The generic representation is unsuitable for displaying a clean enum default in documentation.',
    'object_description should display only the enum raw value and omit its class and member name.',
    reject=r'display only.*raw value|omit.*class.*member name')
review('sympy__sympy-14976', 'MpmathPrinter._print_Rational',
       r'mpf|mpmath~numerator|denominator~(?:before|division|divide|precision)',
       'MpmathPrinter prints rationals as native division, losing precision before mpmath sees them. Convert numerator and denominator to mpf before dividing so evaluation uses arbitrary precision.',
       'In pycode.py emit an mpf numerator divided by an mpf denominator for rational constants. Performing native division first rounds the value before high-precision arithmetic begins.',
       'MpmathPrinter should convert every rational to a Python float at code-generation time to speed up arbitrary-precision evaluation.')
review('sympy__sympy-24066', 'UnitSystem._collect_factor_and_dimension',
       r'dimensionless~(?:dimension\(1\)|unit.dimension|canonical|normaliz|one)~function|argument',
       'Function dimension collection returns compound dimension expressions even when the dimension system considers them dimensionless. Normalize those argument dimensions to Dimension(1) before returning the function result dimensions.',
       'In unitsystem.py canonicalize dimensionless function-argument dimensions to the unit dimension. Use the active dimension system to recognize cancellations instead of retaining a nontrivial dimension expression.',
       'UnitSystem should mark every function argument as dimensionless without consulting the dimension system, even for quantities with physical units.')

review('django__django-11206', 'numberformat.format',
       r'decimal~(?:tiny|small|cutoff|visible|precision)~(?:zero|\b0\b)',
       'numberformat.format can emit scientific notation for a Decimal too small to affect any requested decimal place. Compare its magnitude with the display cutoff and replace it with zero before formatting.',
       'In numberformat.py normalize a tiny Decimal to zero when it lies below the requested visible precision. This avoids exposing an exponent for a value that should display as zero.',
       'The number formatter should round every Decimal to an integer before applying any requested decimal precision.')
review('django__django-11820', 'Model._check_ordering',
       r'\bpk\b|primary.key.alias~(?:meta.pk|resolve|recogniz|alias)~non.relation|nonrelational|scalar|stop|none',
       'Model._check_ordering tries to find pk as a normal field and keeps traversing the model after scalar fields. Resolve the pk alias through _meta.pk and stop model traversal at non-relations so transforms are checked on the field.',
       'In base.py recognize the primary-key alias explicitly during ordering validation. After a scalar field, clear the model context so later segments are interpreted as field transforms.',
       'Model._check_ordering should replace every pk segment with the literal field name id, even for custom primary keys.')
review('django__django-11964', 'Choices.__str__',
    r'choices|enum~(?:str|render|string).{0,65}(?:value|underlying)|(?:value|underlying).{0,65}(?:str|render|string)',
    'Choices inherits enum stringification, displaying the member identity instead of its stored value. Implement __str__ as str(self.value) so model attributes render the underlying choice value.',
    'In enums.py stringify a choice through its value. Templates and other string contexts should receive the stored scalar rather than the enumeration class and member name.',
    'Choices.__str__ should return the human-readable label in every case, replacing the stored choice value during rendering.',
    reject=r'human.readable label.*replacing|replacing.*stored choice value')
review('django__django-11999', 'Field.contribute_to_class',
       r'get.{0,25}display|display.method~(?:preserv|exist|override|hasattr|only)',
       'Field.contribute_to_class installs get_FIELD_display unconditionally, overwriting an existing method. Add the generated method only if the class does not already have that attribute.',
       'In fields/__init__.py preserve an existing choice-display method. Check for the attribute before installing the automatic helper so custom display behavior survives field contribution.',
       'Field.contribute_to_class should overwrite every custom display method to ensure all choice fields use the generated label lookup.')
review('django__django-12325', 'ModelBase.__new__|Options._prepare',
       r'one.to.one|onetoone~parent.link~(?:only|explicit|flag|marked)',
       'ModelBase collects every OneToOneField as a potential inheritance link. Restrict collection to fields explicitly marked parent_link, avoiding misclassification of ordinary one-to-one relations and the later spurious configuration error.',
       'In base.py choose inheritance links only from parent_link=True one-to-one fields. Ordinary relations must not be promoted to parent keys or trigger the obsolete parent-link check in options.py.',
       'ModelBase should mark all one-to-one relations as parent links automatically, even when they are ordinary application relationships.')
review('django__django-14434', '_create_unique_sql',
       r'table~(?:raw|string|unwrapped).{0,60}(?:name|table)|(?:name|table).{0,60}(?:raw|string|unwrapped)~(?:index.columns|column.reference|rename|wrap)',
       '_create_unique_sql passes a Table wrapper where index-column references expect a raw table name. Keep the string name for IndexName, columns and expressions, wrapping it only for the SQL statement table slot.',
       'In schema.py use the raw table-name string in index column references. Delay the Table wrapper until statement formatting so later table-reference and rename operations compare the correct name.',
       'Unique-index SQL should quote the table name repeatedly at every helper call, storing the quoted wrapper as the column-reference identity.')
review('django__django-15037', 'inspectdb.Command.handle_inspection',
       r'to.field~(?:non.primary|not.{0,20}primary|different.{0,20}primary|non.pk)|referenced.column~foreign.key',
       'inspectdb omits to_field for foreign keys targeting a non-primary column. Compare the referenced column with the target table primary key and emit to_field when they differ.',
       'In inspectdb.py preserve foreign-key targets that are not the related primary key. Set to_field to the referenced column instead of generating a relation that silently points at the primary key.',
       'inspectdb should always use the source column name as to_field, regardless of which target column the database constraint references.')
review('django__django-15252', 'MigrationExecutor.migrate',
       r'empty.plan|no.migration|plan.{0,10}\[\]~(?:avoid|skip|not.create|without|return).{0,80}(?:table|schema|state)|(?:table|schema).{0,80}(?:avoid|skip|not.create)',
       'MigrationExecutor.migrate creates the recorder table even for an explicitly empty plan. If no migrations are planned and that table is absent, return the project state without creating the schema.',
       'In executor.py avoid creating django_migrations when the supplied plan is empty. With no recorder table and no work to apply, construct an unapplied project state and return.',
       'MigrationExecutor.migrate should drop the recorder table whenever the migration plan is empty to reset migration history.')
review('django__django-16938', 'Serializer.handle_m2m_field',
       r'select.related~only.{0,15}pk|primary.key.only|defer~(?:reset|replace|unrestricted|no.argument|without.argument)',
       'Many-to-many serialization combines manager-provided explicit select_related fields with only(pk), causing deferred-and-traversed conflicts. Reset to unrestricted select_related() before the primary-key projection in both Python and XML serializers.',
       'In python.py and xml_serializer.py replace the explicit related-field selection with a no-argument select_related call before only(pk). This avoids forcing traversal of fields deferred by the many-to-many key-only query.',
       'The many-to-many serializers should remove only(pk) and serialize every related field recursively, ignoring the key-only serialization contract.')
review('django__django-16950', 'BaseInlineFormSet.add_fields',
       r'non.primary|not.{0,20}primary|non.pk~(?:preserv|retain|not.clear|keep).{0,75}(?:key|default|value)|(?:key|default|value).{0,75}(?:preserv|retain|not.clear|keep)~bound|data|submitted',
       'BaseInlineFormSet.add_fields clears any defaulted parent target while adding fields. Preserve a non-primary target key when form data is provided; clear defaults only for the parent pk or unbound forms.',
       'In forms/models.py keep the generated non-primary parent key for a bound inline form. Clearing that value despite submitted data breaks a foreign key targeting a non-pk field.',
       'BaseInlineFormSet should clear every defaulted parent key on every submission so new random values are always generated.')
review('matplotlib__matplotlib-24149', 'Axes._convert_dx',
       r'no.finite|all.nan|nonfinite|non.finite~stopiteration|first.element|first.item~fallback|fall.back|catch|handle',
       'Axes._convert_dx assumes a finite element exists in original and converted coordinates. Catch StopIteration from each search and fall back to the first element so all-NaN input does not crash conversion.',
       'In _axes.py handle coordinate arrays with no finite values. If the finite-element search is exhausted, use the first item as the conversion reference rather than propagating StopIteration.',
       'Axes._convert_dx should replace all nonfinite coordinates with zero before drawing, making missing bars visible at the origin.')
review('matplotlib__matplotlib-25122', '_spectral_helper',
       r'window~(?:remove|without|not|instead).{0,55}(?:abs|absolute)|(?:abs|absolute).{0,55}(?:remove|incorrect|sum)~sum|normaliz',
       '_spectral_helper normalizes using absolute window values, changing signed-window scaling. Use window.sum and sums of window squared without abs in magnitude, complex and power normalization.',
       'In mlab.py remove the absolute-value conversion from spectral window normalization. Sum the actual window values, or their squares as appropriate, to preserve the intended scaling.',
       '_spectral_helper should normalize every spectrum by the number of FFT bins and ignore the selected window entirely.')
review('pydata__xarray-4966', 'UnsignedIntegerCoder.decode',
    r'unsigned~false~signed~fill.value|fillvalue',
    'UnsignedIntegerCoder.decode handles signed-to-unsigned but not unsigned storage marked _Unsigned=false. Convert that data lazily to a same-width signed dtype and convert _FillValue to the signed type too.',
    'In variables.py support the false unsigned marker on unsigned integer arrays. Reinterpret them as signed data of the same width and keep the fill value in that signed representation.',
    'UnsignedIntegerCoder should ignore _Unsigned=false and keep both data and fill values unsigned in every case.',
    reject=r'ignore.*unsigned.*false|keep.*unsigned')
review('sphinx-doc__sphinx-9367', 'Unparser.visit_Tuple',
       r'tuple~single|one.element|one.item~comma',
       'Unparser.visit_Tuple renders a one-element tuple without the required comma, turning it into a scalar expression. Add a singleton branch emitting (element,) while preserving empty and multi-element tuple syntax.',
       'In ast.py retain the trailing comma when unparsing a tuple with one item. Parentheses alone group an expression and do not preserve the tuple type.',
       'Unparser.visit_Tuple should drop parentheses around every tuple, including tuples used as function arguments.')
review('sympy__sympy-12481', 'Permutation.__new__',
       r'cycle~(?:allow|accept|permit).{0,65}(?:repeat|overlap|duplicate)|(?:repeat|overlap|duplicate).{0,65}(?:allow|accept|permit)~array|non.cycle|notation',
       'Permutation rejects repeated elements even in cycle notation, where overlapping cycles are valid compositions. Apply duplicate rejection only to non-cycle array input and allow overlap in cycle input.',
       'In permutations.py permit repeated elements across composed cycles. Keep duplicate validation for array notation, where repetition really is invalid.',
       'Permutation should accept duplicate elements in ordinary array notation and silently remove repeats to form a smaller permutation.')
review('sympy__sympy-24562', 'Rational.__new__',
       r'denominator~(?:separate|accumulat|integer).{0,65}(?:factor|denominator|\bq\b)|(?:factor|denominator).{0,65}(?:separate|accumulat|integer)~string|text|repeat',
       'Rational.__new__ multiplies the raw denominator by a numerator factor before converting it, repeating strings instead of multiplying numerically. Accumulate denominator factors separately and normalize both operands before combining them.',
       'In numbers.py keep a separate integer denominator accumulator while converting rational inputs. Multiplying an unparsed string denominator repeats its text and yields a wrong fraction.',
       'Rational.__new__ should parse both arguments as binary floats first and use the rounded floating quotient as the exact rational result.')
review('astropy__astropy-7671', 'minversion',
       r'version~(?:numeric|dotted).{0,40}(?:prefix|part)|(?:prefix|part).{0,40}(?:numeric|dotted)~dev|rc|suffix|looseversion',
       'minversion passes development suffixes into LooseVersion comparisons that can mix strings and integers. Extract the required version numeric dotted prefix before comparison, avoiding dev or rc token TypeErrors.',
       'In introspection.py compare against the numeric prefix of the requested minimum version. Removing its development suffix avoids incompatible token types in LooseVersion.',
       'minversion should compare version strings lexicographically without parsing numbers, treating 1.10 as older than 1.9.')
review('django__django-12741', 'BaseDatabaseOperations.execute_sql_flush',
       r'connection.alias~transaction|atomic~(?:remove|drop|derive|own|using)',
       'execute_sql_flush takes a separate database alias even though it already owns a connection. Use self.connection.alias for the atomic block and remove the redundant using argument from the method and caller.',
       'In operations.py bind the flush transaction to the connection own alias. Drop the extra database parameter so the cursor and transaction cannot accidentally refer to different databases.',
       'execute_sql_flush should always open its transaction on the default database while executing statements through the selected connection.')
review('django__django-13089', 'DatabaseCache._cull',
       r'fetchone|fetched.row|boundary.row~none|missing|absent|empty~guard|check|skip|only',
       'DatabaseCache._cull indexes fetchone() without checking whether the boundary row exists. Guard the fetched result and issue the deletion only when a row was returned, avoiding a crash after concurrent cache changes.',
       'In cache/backends/db.py check the culling boundary fetch before subscripting it. An absent row should skip the range deletion rather than dereference None.',
       'DatabaseCache._cull should delete the entire cache table whenever the boundary query returns no row.')
review('django__django-13279', 'SessionBase.encode|SessionBase._legacy_encode',
       r'sha1~legacy|old.format~base64|hash|serializ',
       'SessionBase.encode always uses the new signing format. When DEFAULT_HASHING_ALGORITHM is sha1, emit the legacy base64-encoded hash:serialized-data format so older deployments can still read sessions.',
       'In sessions/backends/base.py retain old-format encoding during a sha1 compatibility period. Serialize the session, prefix its legacy hash, and base64-encode the combined payload.',
       'SessionBase.encode should remove session authentication hashes entirely so every older version can decode the raw serialized dictionary.')

review('django__django-13809', 'runserver.Command.add_arguments|runserver.Command.inner_run',
       r'skip.checks~(?:flag|option|argument)~(?:guard|skip|conditional|unless|only).{0,65}(?:system|check)|(?:system|check).{0,65}(?:guard|conditional)',
       'runserver does not expose or honor skip_checks for its explicit startup checks. Add the flag and guard the system-check message and call with it, leaving migration checking in place.',
       'In runserver.py register the skip-checks option and conditionally bypass the startup system check. The command currently runs that check regardless of the requested skip behavior.',
       'runserver should remove all startup checks permanently, including migration checks, because development servers do not need validation.')
review('django__django-14311', 'get_child_arguments',
       r'spec.name|module.name~parent~__main__|main.module|ordinary.module',
       'get_child_arguments always restarts spec.parent, which is wrong for an ordinary module launched with -m. Use spec.name except for package __main__ modules, where the parent package is the runnable target.',
       'In autoreload.py distinguish a package main module from an ordinary module. Restart the full module name for normal -m execution and use the parent only for __main__ entry points.',
       'get_child_arguments should always restart the parent package, even when a specific non-main submodule was originally executed.')
review('django__django-14351', 'In.get_group_by_cols',
       r'group.by~(?:rhs|right.hand|subquery)~(?:pk|primary.key)~select|project',
       'In grouping collects right-hand query columns before ensuring its default projection. Add get_group_by_cols that selects pk when the RHS has no explicit select fields, then combines grouping columns from both sides.',
       'In lookups.py prepare an IN subquery primary-key selection before asking it for group-by columns. An unprojected RHS otherwise contributes incorrect columns to grouping.',
       'In.get_group_by_cols should add every column of the right-hand model to GROUP BY regardless of the selected subquery output.')
review('django__django-16116', 'makemigrations.Command.handle',
       r'check~(?:before|without|avoid|prevent).{0,65}(?:writ|file)|(?:writ|file).{0,65}(?:before|avoid|prevent)~exit|nonzero|status',
       'makemigrations --check writes migration files before exiting with failure for pending changes. Exit with status one before either write path so check mode reports missing migrations without creating files.',
       'In makemigrations.py handle the check-only failure before writing or updating migrations. Pending changes should cause a nonzero exit with no generated files.',
       'makemigrations --check should always write the missing migrations and then exit successfully because the repository has been repaired.')
review('django__django-16145', 'runserver.Command.inner_run',
       r'0\.0\.0\.0~(?:display|print|message|url)~(?:\b0\b|wildcard|shorthand)',
       'runserver displays the address shorthand 0 literally in its startup URL. Format that wildcard address as 0.0.0.0 while retaining IPv6 brackets and other addresses unchanged.',
       'In runserver.py expand the zero-address shorthand for the displayed server URL. The listening behavior stays the same, but the message should show the full wildcard IPv4 address.',
       'runserver should change a wildcard bind into localhost-only binding whenever the user supplies address zero.')
review('pydata__xarray-4075', 'Weighted._sum_of_weights',
       r'bool~(?:cast|convert).{0,50}(?:int|integer)|(?:int|integer).{0,50}(?:cast|convert)~sum|count|dot',
       'Weighted._sum_of_weights applies dot to boolean weights, producing boolean truth instead of a count. Cast boolean weights to integers before reduction so the denominator sums all valid weights.',
       'In weighted.py convert boolean weights to integer values when computing their sum. Boolean dot products collapse multiple true entries to true rather than counting them.',
       'Weighted._sum_of_weights should convert all numeric weights to booleans so only their truth value contributes to weighted means.')
review('sympy__sympy-17318', '_sqrt_match',
       r'square|squared~positive~rational~(?:guard|require|only|check).{0,80}(?:split.surds|surd|positive)|(?:split.surds).{0,80}(?:guard|require|only)',
       '_sqrt_match sends rational but negative term squares into split_surds, whose algorithm assumes positive rationals. Require every squared term to be both rational and positive before using that branch.',
       'In sqrtdenest.py guard the surd-splitting path with positivity of all rational squares. Complex terms with negative squares violate the helper precondition and must not enter it.',
       '_sqrt_match should replace negative squared terms by their absolute values and continue denesting as if the expression were real.')
review('sympy__sympy-18211', 'Relational._eval_as_set',
       r'conditionset~notimplemented|unsupported|unsolv|cannot.solve~real',
       'Relational._eval_as_set propagates NotImplementedError when the inequality solver cannot solve a relation. Return ConditionSet over the reals with the original condition as a symbolic fallback.',
       'In relational.py preserve an unsolved real relation as a ConditionSet. Catch the unsupported-solver case instead of failing set conversion or guessing its solutions.',
       'Relational._eval_as_set should return the empty set whenever the solver cannot find an explicit solution.')
review('sympy__sympy-19954', 'PermutationGroup.minimal_blocks',
       r'block~(?:mask|defer|after).{0,60}(?:remov|delet|filter)|(?:remov|delet|filter).{0,60}(?:mask|defer|after)~parallel|align|three|list',
       'minimal_blocks deletes from parallel block lists while iterating representative blocks, shifting indices. Mark removals in a mask and filter all three lists afterward so their entries remain aligned.',
       'In perm_groups.py defer removing nonminimal block systems until comparison ends. Apply one removal mask to each parallel list instead of deleting by shifting indices during iteration.',
       'minimal_blocks should sort each of its three block lists independently after every deletion to restore their correspondence.')
review('sympy__sympy-24539', 'PolyElement.as_expr',
       r'(?:supplied|provided|custom|keep).{0,65}(?:preserv|retain|keep|use)|(?:preserv|retain|keep|use).{0,65}(?:supplied|provided|custom)~default|absent|omitted|none',
       'PolyElement.as_expr replaces valid supplied symbols with ring defaults because the else branch covers valid input. Use defaults only when no symbols are passed and otherwise preserve the supplied symbols after length validation.',
       'In rings.py keep the caller-provided symbols when their count matches the generators. Fall back to ring symbols only for omitted input, rather than overwriting a valid substitution.',
       'PolyElement.as_expr should always ignore caller-provided symbols and use the ring generators to guarantee a canonical expression.')
review('astropy__astropy-7166', 'InheritDocstrings',
    r'descriptor|propert~docstring|documentation~(?:include|recogniz|inherit|extend)',
    'InheritDocstrings processes functions only, excluding properties and other data descriptors. Include inspect.isdatadescriptor in the eligibility check so undocumented public descriptors inherit base documentation.',
    'In misc.py extend docstring inheritance to data descriptors as well as methods. A public property without documentation should receive the corresponding base-class docstring.',
    'InheritDocstrings should overwrite every existing property docstring with the nearest base-class documentation.',
    reject=r'overwrite.*existing.*docstring')
review('astropy__astropy-7606', 'UnitBase.__eq__|UnrecognizedUnit.__eq__',
    r'notimplemented~(?:convert|coerc|unsupported|other).{0,60}(?:unit|operand|fail)|(?:unit|operand).{0,60}(?:convert|coerc|unsupported|fail)~equal|compar|reflected',
    'Unit equality returns False or raises when coercion of the other operand fails, preventing reflected comparison. Return NotImplemented on conversion errors in both ordinary and unrecognized unit equality.',
    'In units/core.py let the other operand handle unsupported equality by returning NotImplemented. Failed unit conversion must not force False or leak an exception before reflected comparison can run.',
    'Unit equality should treat every unconvertible object as equal to an unrecognized unit because neither has a known physical dimension.',
    reject=r'treat.*unconvertible.*equal|unconvertible.*equal.*unrecognized')
review('django__django-11141', 'MigrationLoader.load_disk',
       r'migration~(?:namespace|__file__)~(?:discover|enumerat|names|empty|ignore.no.migrations)',
       'MigrationLoader.load_disk rejects packages without __file__ before discovering migrations. Enumerate package modules first, then classify the app using discovered migration names and ignore_no_migrations so namespace packages can supply migrations.',
       'In loader.py do not infer that an app is unmigrated solely from a missing __file__. Inspect its package path for migration modules and apply the empty-package policy after discovery.',
       'MigrationLoader.load_disk should mark every namespace package migrated without inspecting whether it contains any migration modules.')
review('django__django-11333', 'get_resolver|_get_cached_resolver|clear_url_caches',
       r'urlconf~(?:before|resolved|concrete|actual).{0,55}(?:cache|cach)|(?:cache|cach).{0,55}(?:resolved|concrete|actual)~none|default|settings',
       'get_resolver caches under None before resolving ROOT_URLCONF, reusing stale resolvers after settings change. Resolve the default first and cache by the actual URLconf in a helper, updating cache clearing to target that helper.',
       'In resolvers.py normalize an omitted URLconf to the current setting before the cache lookup. A concrete URLconf cache key prevents a previous default resolver being reused for a new setting.',
       'get_resolver should retain a single global resolver forever and ignore ROOT_URLCONF changes once any URL has been resolved.')
review('django__django-11734', 'RelatedLookupMixin.get_prep_lookup|Query.split_exclude',
       r'outerref~(?:nest|wrap|scope)~resolve.expression|(?:skip|bypass|avoid).{0,55}(?:prep|coerc|literal)',
       'Related lookups coerce expression RHS values as literals, while split_exclude loses an existing OuterRef scope. Skip preparation for resolve_expression objects and wrap existing OuterRef in another OuterRef for the new subquery level.',
       'In related_lookups.py bypass literal field preparation for expression operands. In query.py preserve a nested OuterRef when exclusion adds another query scope, instead of flattening its reference.',
       'Related lookups should resolve every OuterRef immediately against the innermost query and convert it to an integer primary key.')
review('django__django-11749', 'call_command',
       r'mutually.exclusive|required.group~(?:option|keyword|kwargs)~(?:pass|forward|include).{0,60}(?:pars|argument)|(?:pars).{0,60}(?:pass|forward|include)',
       'call_command forwards individually required keyword options to argparse but misses members of required mutually exclusive groups. Include supplied group members in parse_args so the parser sees the satisfied requirement.',
       'In management/__init__.py pass keyword-provided options from required exclusive groups into argument parsing. Their own required flag is false even though the group requires one member.',
       'call_command should pass every member of a mutually exclusive group simultaneously so the parser always sees at least one required option.')
review('django__django-11848', 'parse_http_date',
       r'(?:50|fifty)~(?:current|rolling).{0,30}(?:year|century)|(?:year|century).{0,30}(?:current|rolling)~past|previous|future',
       'parse_http_date expands two-digit years using a fixed 1970 cutoff. Use the current century and interpret years more than fifty years ahead as belonging to the previous century, following the HTTP rolling-window rule.',
       'In http.py resolve short years relative to the current year. If the apparent date is over 50 years in the future, move it to the previous century instead of using a permanent cutoff.',
       'parse_http_date should map every two-digit year into the twentieth century regardless of the current date.')
review('django__django-12754', 'generate_created_models',
       r'base|parent~(?:remov|delet).{0,50}field|field.{0,50}(?:remov|delet)~dependenc|before~child|subclass|new.model',
       'generate_created_models depends on base-model creation but not removal of inherited fields reused by the child. Add dependencies on those removals so a new subclass does not clash with still-present base fields.',
       'In autodetector.py create the child only after conflicting fields have been removed from its parent. Record removal dependencies for names shared by removed base fields and the new model fields.',
       'generate_created_models should rename every child field that matches a removed parent field instead of ordering the migrations correctly.')
review('django__django-13551', 'PasswordResetTokenGenerator._make_hash_value',
       r'email~(?:hash|token)~(?:include|add|incorporat|invalidat)',
       'PasswordResetTokenGenerator omits email from its state hash, so changing the email leaves existing reset tokens valid. Include the configured email-field value, with an empty fallback, in the token hash input.',
       'In tokens.py incorporate the user email into password-reset token state. An address change should invalidate previously issued tokens even when password and login time remain unchanged.',
       'PasswordResetTokenGenerator should remove the password from the hash and bind tokens only to the user primary key forever.')
review('django__django-13837', 'get_child_arguments',
       r'__spec__|module.spec~(?:parent|package)~(?:instead|hardcod|django.__main__|general|custom)',
       'get_child_arguments recognizes module execution only by comparing with django.__main__. Use the running __main__.__spec__.parent as the -m target so custom package entry points reload correctly too.',
       'In autoreload.py derive a package restart target from the running module spec instead of hardcoding Django main-file identity. A custom package launched with -m needs its own parent package preserved.',
       'get_child_arguments should always restart with -m django regardless of which custom package launched the development server.')
