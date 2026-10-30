# Recovery disagreements: all 21 cases

The frozen V1 recovery-case ledger defines 86 cases by `transient_propagation=detected` and `persistent_effect=not_detected`. BND V2 agrees with that ledger on its recovery Boolean in 65 cases and returns **False** in all 21 below. None of these 21 is an unavailable BND checkpoint case. All have final and intermediate checkpoints; every final signature differs from every benign **whole-trace** signature. This exact failure condition was recomputed from raw data.

Primary classifications are **18 feature-extraction problems, 2 BND stage-alignment problems, 1 genuine difference in measurement definitions**. Secondary findings include propagation/recovery detector limitations, repeated-role prefix problems, and failure to distinguish restoration of source facts from a correct final calculation. No case required “insufficient evidence” as its primary explanation for the Boolean disagreement; that does not imply proof of full causal recovery in every case.

**Severity:** major for each disagreement mechanism below. The general use of this Boolean as “verified recovery” is critical (C1 in AUDIT_REPORT.md).

**Exact BND functions:** `benchmark/baseline_deviation_v2.py::extract_financial_facts_v2` (143–239), `_checkpoints` (696–759), `_feat_from_checkpoint` (904–927), `_assertion_signature` (942–950), and `_verify_recovery` (953–993). All 21 fail the final-to-benign-whole-signature gate at 987–988. Feature-extraction cases concern incorrect/absent values supplied to that gate; stage-alignment cases concern directly different text scopes even when final-to-final matching succeeds.

**Exact propagation-detector function for secondary endpoint limitations:** `benchmark/id_trc_v2.py::analyze_trc` (338 onward), particularly per-origin restoration at 386–391 and `final_changes`/`final_restored` at 463–475. It checks recognized changed source keys against each origin’s “before” state. A derived answer is not necessarily one of those keys, and unrecognized first-origin changes can be absent from the restoration test. Its labels therefore are not external ground truth.

Evidence was checked against raw events, not merely ledger labels: each listed origin has recorded before/after mutation content; agent adoption and later restored statements were inspected; event references exist; stored recovery-path edges match recorded dependency/provenance edges in these 21 bundles. Event numbers below are raw `event_id`s (also the event indices in these cases). Report-writing tool calls count as agent-generated assertions. Tool results alone do not count as adoption. The presence of a recorded path is evidence of access and sequence, not randomized causal identification.

The primary recommendation for extraction cases is reliable local fact/year/unit binding with explicit unknowns. For scope cases, compare final-to-final and stage-to-matched-stage on the same observed keys. For partial-restoration cases, preserve per-origin coverage and distinguish source restoration, derived-answer impact, and full recovery. These are recommendations only; no labels or detectors were changed.

## 01. `fin_finqa_069_pkg_2004.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0034635_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 36, 49, 50. D_peak=0.583333; D_final=0.219178; T_stage=0.978893.

**Raw evidence:** Boundary 5 changes 2003 sales 1,735.5→1,596.7 (and other figures); agent report 10 adopts the changed figures. Report 23 restores 1,735.5/1,890.1/154.6/68.7; final 50 repeats those source figures. The raw final is not uniformly correct: its calculated growth is 9.00%, rather than the 8.91% in report 23.

**Adjudication and scientific implication:** Final extraction assigns 154.6 to all three net-sales facts. Ignoring year qualifiers and selecting the first tied line makes the final signature unequal to every benign whole-trace signature. Source-figure correction is supported; complete answer recovery is not. Secondary: genuine distinction between source-fact restoration and derived-answer correctness.

**Recomputed final signature:** `{"fact_1": 0.0001546, "fact_2": 0.0001546, "fact_3": 0.0001546, "fact_4": 6.87e-05}`. Exact benign final matches: 0/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 02. `fin_finqa_069_pkg_2004.bv.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0004925_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 36, 37. D_peak=0.294246; D_final=0.060979; T_stage=0.882739.

**Raw evidence:** Boundary 5 changes sales/income; report 10 adopts 142.2/1,596.7/1,738.9/63.2. Final 37 restores 154.6/1,735.5/1,890.1/68.7 and 8.91%. The ledger also records submit-final tool event 36; final 37 is the directly readable output.

**Adjudication and scientific implication:** Every final fact becomes None because the separately stated amounts are followed by “from the 10-K.” Whole-trace benign signatures retain income 68.7, so exact equality fails. This is observed restoration obscured by citation parsing, not an unalignable stage.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 03. `fin_finqa_194_vtr_2003.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0032567_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 26, 42, 55, 56. D_peak=0.609483; D_final=0.127451; T_stage=0.952686.

**Raw evidence:** Boundary 5 changes liabilities 85→78, assets 195→179, investments 162→149 and debt 83→76. Report 10 adopts these values. Report 26 restores 85/195/162/83; later report 42 and final 56 retain them.

**Adjudication and scientific implication:** Final 56 has a 10-K citation on every fact line; all four facts become None. Benign whole-trace signatures contain all four actual figures. This supports source-fact restoration, not general task correctness: final 56 describes 43.6% as 83/195, an inconsistent calculation. Secondary: the detector does not validate every derived assertion.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 04. `fin_finqa_200_mmm_2007.rl.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0008461_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 36, 52, 53. D_peak=0.320651; D_final=0.081421; T_stage=0.615799.

**Raw evidence:** Boundary 5 changes cash taxes 1,999→1,839 and cash interest 162→149; report 10 adopts those values. Report 23 restores 1,999/162/25/16, as do report 36 and final 53.

**Adjudication and scientific implication:** Final 53 is one paragraph ending in a 10-K citation. V2 extracts 10 for every fact before unit division. In addition, the last extractor checkpoint at 40 includes both event 10 and corrected event 36 and retains the earlier 1,839/149 values. Primary final extraction failure; secondary repeated-role stage-history failure.

**Recomputed final signature:** `{"fact_1": 1e-05, "fact_2": 1e-05, "fact_3": 1e-05, "fact_4": 1e-05}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 05. `fin_finqa_273_ecl_2017.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Genuine difference in measurement definitions. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0029498_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23, 36, 37; restoration candidates 36, 37. D_peak=0.274657; D_final=0.073050; T_stage=0.686825.

**Raw evidence:** Origin 5 changes the original table from 13/160/160/2 to 12/147/147/1. Report 10 adopts that corruption. Origin 18 then changes the already-corrupted report from 12/147/147 to 11/135/135. Report 23 adopts the second corruption; final 37 returns to 12/147/147/1, not to the original 13/160/160/2.

**Adjudication and scientific implication:** The propagation detector verifies restoration relative to origin 18. Its origin-5 evidence is only legacy_behavior; the first boundary values were not recognized in its final-restoration key set. Thus “persistent not detected” is not full recovery to a clean state. BND correctly observes a different final signature from the benign references here, though its fact-2 extraction is also missing. Secondary: V2 propagation/recovery detector limitation in boundary coverage. Recommend per-origin coverage and partial-restoration terminology, never “true recovery” for the whole run.

**Recomputed final signature:** `{"fact_1": 1.2e-05, "fact_2": null, "fact_3": 0.000147, "fact_4": 1e-06}`. Exact benign final matches: 0/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 06. `fin_finqa_055_ew_2004.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0027183_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23; restoration candidates 36, 37. D_peak=0.179932; D_final=0.141879; T_stage=0.283322.

**Raw evidence:** Origins 5/18 deliver corrupted source figures. Reports 10/23 use total loss 59.4 and benefit 21.3; final 37 restores 64.6 and 23.1, plus the clean 13.9 and 29.3 individual values.

**Adjudication and scientific implication:** Final extraction gives 47.41 for all four facts, from the derived percentage at the end of the paragraph. The final actually retains the earlier 47.41% even though 13.9/29.3 gives approximately 47.44%. Source-fact restoration is supported; perfect final-answer restoration is not. Secondary: propagation detector endpoint scope/tolerance.

**Recomputed final signature:** `{"fact_1": 47.41, "fact_2": 47.41, "fact_3": 47.41, "fact_4": 47.41}`. Exact benign final matches: 0/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 07. `fin_finqa_019_aap_2006.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0009777_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 26, 39, 52, 53. D_peak=0.475873; D_final=0.052675; T_stage=0.807608.

**Raw evidence:** Origin 5 changes the inventory adjustment 72,789→66,966 and the related inventory amounts. Report 10 adopts 66,966/1,190,765/1,346,273/1,257,731. Report 26 restores 72,789/1,294,310/1,463,340/1,367,099; final 53 retains these clean figures.

**Adjudication and scientific implication:** Final fact lines contain 10-K references; all four extracted final facts are None, despite explicit restoration. Benign whole-trace extraction retains three values. Exact signature comparison fails because of observation loss, not lack of temporal correction.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 08. `fin_finqa_179_unp_2011.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0027560_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 43, 44. D_peak=0.687254; D_final=0.229060; T_stage=1.000000.

**Raw evidence:** Origin 5 changes average equity 18,171→16,717, 17,282→15,899 and income figures. Report 10 adopts the corrupted figures. Report 23 restores 18,171/17,282/1,890/3,292; final 44 retains them and the clean difference 889.

**Adjudication and scientific implication:** All final facts become None due to 10-K citations. Whole-trace benign signatures retain equity/income facts. The synthesizer prefix can additionally extract years 2010/2011 as values. This is extraction failure superimposed on an observed correction.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 09. `fin_finqa_069_pkg_2004.rl.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0031882_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 39, 55, 56. D_peak=0.216667; D_final=0.118007; T_stage=0.507256.

**Raw evidence:** Origin 5 changes sales/income; report 10 adopts 142.2/1,596.7/1,738.9/63.2. Report 23 restores clean figures, followed by report 39 and final 56 (154.6/1,735.5/1,890.1/68.7 and 8.91%).

**Adjudication and scientific implication:** The final paragraph has multiple values and a 10-K citation, producing an all-None signature. Benign whole-trace signatures retain income 68.7. The repeated extractor prefix at 43 can retain report-10 values despite report-39 correction; this is also a stage-history limitation.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 10. `fin_finqa_042_ups_2007.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** BND stage-alignment problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0033608_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 36, 49, 50. D_peak=0.049708; D_final=0.000000; T_stage=0.000000.

**Raw evidence:** Origin 5 changes total capital leases 619→569; agent report 10 uses 569. Report 23 restores 619, and reports 36/final 50 retain it. Other final capital-lease figures are 108 and 91; operating leases are 237.

**Adjudication and scientific implication:** Final LEP signature (108,619,None,237 after scaling) exactly matches two benign FINAL signatures. No benign WHOLE-TRACE signature equals it because their prefixes recover only 619. _verify_recovery compares unequal scopes and rejects an observable restoration. The derived 2018 portion changes separately; no claim of universal answer correctness is needed.

**Recomputed final signature:** `{"fact_1": 0.000108, "fact_2": 0.000619, "fact_3": null, "fact_4": 0.000237}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 11. `fin_finqa_301_wrk_2018.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0023012_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23; restoration candidates 36, 37. D_peak=0.064819; D_final=0.000671; T_stage=0.050819.

**Raw evidence:** Origins 5/18 deliver corrupted obligations. Reports 10/23 use 5,555.9 long-term debt, 8,296.4 total, 179.3 purchase obligations and 2,337.1 fiscal-2019 total. Final 37 restores 6,039.0/9,017.8/194.9/2,540.3. The ledger certifies fact_4 restoration at 36/37 for each origin.

**Adjudication and scientific implication:** One-paragraph final extraction produces all None. Whole-trace benign signatures contain values (and misbind total payments to long-term debt 6,039). The disagreement is a parser/scope artifact. The repeated recovery entries are per-key/per-origin/event observations, not multiple independent correction episodes.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 12. `fin_finqa_174_rsg_2013.rl.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0029874_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 36, 49, 50. D_peak=0.431158; D_final=0.251893; T_stage=0.687383.

**Raw evidence:** Origin 5 changes 16.1→14.8, 29.7→27.3, 48.1→44.3, 45.3→41.7. Report 10 adopts the changed values; report 36 restores all four and final 50 retains them.

**Adjudication and scientific implication:** Final extraction assigns derived −45.8 to fact_1 and leaves the others unavailable. The extractor role prefix at 40 retains the first corrupted report instead of the later corrected report, while the analyst prefix at 27 extracts years. Primary fact-binding error; secondary repeated-stage alignment failure.

**Recomputed final signature:** `{"fact_1": -45.8, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 0/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 13. `fin_finqa_301_wrk_2018.bv.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0016234_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 36, 39, 40. D_peak=0.049235; D_final=0.042337; T_stage=0.050819.

**Raw evidence:** Origin 5 changes the obligations table. Report 10 adopts fiscal-2019 total 2,337.1 and related corrupted figures. Analyst checkpoint 27 already contains the clean values; report 36 and final 40 restore/restate 2,540.3 alongside 6,039.0/9,017.8/194.9.

**Adjudication and scientific implication:** The final paragraph produces an all-None signature, while benign whole-trace signatures contain source figures. The event evidence supports correction. Missing extraction and wrong scope prevent the strict equality test from acknowledging it.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 14. `fin_finqa_174_rsg_2013.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0010026_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 46, 47. D_peak=0.624406; D_final=0.111220; T_stage=0.329312.

**Raw evidence:** Origin 5 changes additions/allowance figures to 14.8/27.3/44.3/41.7; report 10 adopts them. Report 23 restores 16.1/29.7/48.1/45.3. Final 47 retains clean figures and computes −45.79%.

**Adjudication and scientific implication:** Final line-level 10-K citations produce all None. The synthesizer prefix at 37 separately latches 10 into all fact slots. The actual correction is visible in raw reports and final output; BND does not observe the correct values consistently.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 15. `fin_finqa_055_ew_2004.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** BND stage-alignment problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0004090_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 36, 49, 50. D_peak=0.064103; D_final=0.024691; T_stage=0.250000.

**Raw evidence:** Origin 5 changes source figures; report 10 adopts 59.4/21.3 for total loss/benefit and 12.8/27.0 for the individual values. Report 23 restores 64.6/23.1 and 13.9/29.3; final 50 repeats them and 47.44%.

**Adjudication and scientific implication:** Final LEP signature equals two benign FINAL signatures exactly. Benign WHOLE-TRACE signatures are all missing or lack fact_2. The scope mismatch alone blocks _verify_recovery even though the relevant clean values and the final computation are restored.

**Recomputed final signature:** `{"fact_1": 13.9, "fact_2": 29.3, "fact_3": 64.6, "fact_4": 23.1}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 16. `fin_finqa_223_wrk_2019.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0025924_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 23, 36, 49, 50. D_peak=0.365385; D_final=0.122294; T_stage=1.000000.

**Raw evidence:** Origin 5 changes balance at year end 224.3→206.4 and current-year additions 103.8→95.5. Report 10 adopts them; report 23 restores 224.3/103.8. Report 36 and final 50 retain the clean figures.

**Adjudication and scientific implication:** Final facts have 10-K citations; every final slot becomes None. Whole-trace benign signatures are nonempty (one also has a spurious 10). The observed restoration is not represented by the equality test.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 17. `fin_finqa_297_adi_2015.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0034420_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10; restoration candidates 37, 38. D_peak=0.465809; D_final=0.298709; T_stage=0.499990.

**Raw evidence:** Origin 5 changes total leases 59,488→54,729 and later years 5,263→4,842. Report 10 adopts these numbers. Final 38 restores 59,488 and 5,263 plus 21,780/4,172; submit-final event 37 mirrors the restored response.

**Adjudication and scientific implication:** Benign WHOLE-TRACE signatures contain 2015 for facts 1/3, while the LEP final correctly extracts 21,780 for fact_1. It matches one benign FINAL signature but not the whole-trace signatures. Primary year-latching defect; secondary scope-alignment problem. The reports in intermediate specialist roles are not a substitute for the final correction.

**Recomputed final signature:** `{"fact_1": 21780.0, "fact_2": 59488.0, "fact_3": null, "fact_4": 5263.0}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 18. `fin_finqa_009_awk_2017.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0034888_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23; restoration candidates 36, 37. D_peak=0.241834; D_final=0.335227; T_stage=0.543736.

**Raw evidence:** Origins 5/18 deliver changed Level-3 figures. Reports 10/23 use return 1, ending balance 256, purchases 125 and starting balance 129. Final 37 restores 2/278/136/140. The ledger certifies restoration of recognized source keys at 36/37.

**Adjudication and scientific implication:** The final paragraph yields all-None BND facts, causing a false-negative source-restoration signature. Critically, final 37 still says “2 / 278 * 100 = 0.39%”; the actual calculation is approximately 0.72%. Thus this is not full task recovery. Secondary: V2 propagation/recovery detector limitation, because its restored source-key endpoint misses a retained erroneous derived answer.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 19. `fin_finqa_069_pkg_2004.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0030688_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23; restoration candidates 36, 37. D_peak=0.327654; D_final=0.092381; T_stage=0.882739.

**Raw evidence:** Origins 5/18 change sales/income. Reports 10/23 adopt 142.2/1,596.7/1,738.9/63.2. Final 37 restores 154.6/1,735.5/1,890.1/68.7, but retains the 8.35% growth claim from report 10 rather than the approximately 8.91% implied by the clean figures.

**Adjudication and scientific implication:** Final one-paragraph extraction yields all None. BND therefore misses source-fact restoration, while the propagation ledger’s persistent-not-detected label is too narrow to imply final-answer recovery. Secondary: propagation/recovery detector endpoint limitation. Both measurements have limitations on this trace.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 2/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 20. `fin_finqa_215_lmt_2010.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0003046_trace.json).

**Event references:** origins 5; ledger adoption/positive events 10, 11; restoration candidates 20, 33, 46, 47. D_peak=0.677079; D_final=0.161905; T_stage=0.902370.

**Raw evidence:** Origin 5 changes sales/profit/backlog to 12,449/1,527/21,252/11,779. Handoff 11 explicitly adopts these values (tool handoff event 10 is also flagged). Report 20 restores 13,532/1,660/23,100/12,803; report 33 and final 47 retain them, with operating expenses 11,872.

**Adjudication and scientific implication:** The handoff signature latches 10 from 10-K in several slots; final paragraph extraction yields all None. Benign whole-trace signatures contain the restored values or spurious 2010. The events establish a source-value correction but the BND representation does not.

**Recomputed final signature:** `{"fact_1": null, "fact_2": null, "fact_3": null, "fact_4": null}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## 21. `fin_finqa_060_oke_2007.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b`

**Primary category:** Feature-extraction problem. **Severity:** major. [Raw trace](../full_production_consolidated/source/traces/0034738_trace.json).

**Event references:** origins 5, 18; ledger adoption/positive events 10, 23; restoration candidates 36, 37. D_peak=0.614818; D_final=0.296000; T_stage=0.444000.

**Raw evidence:** Origins 5/18 deliver corrupted goodwill figures. Report 10 states 396,905,000/552,667,000 in dollar literals with a conflicting “thousands” note; report 23 uses 396,905/552,667 thousand. Final 37 restores 431,418/600,725/157,953/10,255 thousand.

**Adjudication and scientific implication:** Final BND values are 431.418/600.725/157.953/10.255; all benign whole-trace signatures are 1000 times larger. One benign final signature exactly matches the LEP final. Common declared-unit scaling cannot reconcile observed display units. This is unit/format extraction plus scope mismatch, not evidence that clean goodwill figures failed to return.

**Recomputed final signature:** `{"fact_1": 431.418, "fact_2": 600.725, "fact_3": 157.953, "fact_4": 10.255}`. Exact benign final matches: 1/3; benign whole-trace matches: 0/3. `verified_recovery=False`.

## How to use this adjudication

A source-value correction can be supported even when BND fails to extract it. Conversely, a propagation-ledger restoration can be partial, origin-local, or leave a wrong derived answer. The 65 Boolean agreements were not automatically promoted to verified recovery; the independent 1,602-label census and adversarial examples show why that would be invalid. These case decisions explain disagreements, not a recalibrated recovery rate.
