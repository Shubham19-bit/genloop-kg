| Type | N | Accuracy | Precision | Recall |
|---|---|---|---|---|
| single_fact | 10 | 90.00% | 90.00% | 90.00% |
| multi_hop | 10 | 90.00% | 98.89% | 96.02% |
| comparison | 10 | 90.00% | 90.00% | 90.00% |
| overall | 30 | 90.00% | 92.96% | 92.01% |

Failures: 3
- Q10 (single_fact): gold=`47,400` system=`No answer found in the graph.`
- Q18 (multi_hop): gold=`$658,124 million` system=`$595,545 million`
- Q24 (comparison): gold=`LLY` system=`` error=`execution failed: unknown company ''`