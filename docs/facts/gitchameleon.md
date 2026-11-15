# GitChameleon 2.0 facts (checked 15 Nov 2026)

- Paper: "GitChameleon 2.0: Evaluating AI Code Generation Against Python Library Version Incompatibilities", ACL 2026 Main,
  arXiv 2507.12367. Harness repo: `mrcabbage972/GitChameleonBenchmark` (★12, updated 2026-10-03). Site:
  gitchameleon-2-0.github.io. Cite it in the README.
- Dataset: HF `cabbage972/GitChameleon-2.0` — files `dataset.jsonl` (328 rows), `ground_truth_solutions.jsonl`,
  `hidden_tests/test_sample_<id>.py`. Load: `load_dataset("cabbage972/GitChameleon-2.0", "problems")`.
- Row fields: `python_version` (3.10: 219, 3.7: 67, 3.9: 42), `library`, `version`, `problem`, `starting_code`,
  `example_id`, `test`, `solution`, `type_of_change`, `name_of_class_or_func`, `additional_dependencies` (e.g.
  `scipy==1.7.3 numpy==1.21.6`), `functional`, `webdev`, `solution_api_call`, `api_calls` (e.g. `['torch.from_numpy',
  'scipy.special.i1']`), `release_date`, `docs` (official docs URLs for that version).
- 81 distinct (library, version) pairs. Top libraries: librosa 42, scipy 40, sympy 33, flask 22, falcon 21, torch 18,
  numpy 15, scikit-learn 12, pandas 10, django 10, mitmproxy 10, networkx 9.
- `type_of_change`: argument change 59, name change 52, new func/method/class 46, argument or attribute change 44, new
  feature 40, breaking change 15, output behaviour 13, deprecation 13, other library 8, other 7+.
- Harness: Docker image `mrcabbage972/gitchameleon:latest` + `make evals-setup` builds one venv PER PROBLEM
  (`gitchameleon/create_venvs.py --start 0 --end 1000 --base_path .dataset_venvs`), then
  `evaluate --solution-path SOLUTION_PATH [--workers N]` prints success rates. Heavy (torch venvs are GBs): dedupe venvs
  by (python_version, library==version, additional_dependencies) and run big sweeps on a cloud box.

## Rules for us
- `solution`, `ground_truth_solutions.jsonl` and `hidden_tests/` are the ANSWER KEY. Nothing from them may enter the graph
  or a prompt. Only `problem`, `starting_code`, `library`, `version`, `python_version`, `additional_dependencies` go to the
  solver. (`docs` URLs only in the "docs" baseline arm, clearly labelled.)
- The graph learns ONLY from: real library code of each version (ingestion), and pass/fail + error text of OUR solver's
  runs on the TRAIN split.
