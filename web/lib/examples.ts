// On-page examples: each one is exactly what the live Pinned graph returns for that code and version.
export type Example = {
  lib: string;
  version: string;
  file: string;
  before: string[];
  bad: string; // the offending call, highlighted in `before`
  call: string; // dotted path
  removedIn: string;
  fix: string;
  fixCode: string;
  after: string[];
};

export const EXAMPLES: Example[] = [
  {
    lib: "pandas", version: "2.0.3", file: "report.py",
    before: ["import pandas as pd", "", "df = pd.DataFrame({\"total\": [10]})", "df = df.append({\"total\": 12}, ignore_index=True)"],
    bad: "df.append", call: "pandas.DataFrame.append", removedIn: "2.0",
    fix: "pandas.concat", fixCode: "pd.concat([df, pd.DataFrame([{\"total\": 12}])], ignore_index=True)",
    after: ["import pandas as pd", "", "df = pd.DataFrame({\"total\": [10]})", "df = pd.concat([df, pd.DataFrame([{\"total\": 12}])], ignore_index=True)"],
  },
  {
    lib: "networkx", version: "3.0", file: "graph.py",
    before: ["import networkx as nx", "", "g = nx.Graph([(1, 2), (2, 3)])", "m = nx.to_numpy_matrix(g)"],
    bad: "nx.to_numpy_matrix", call: "networkx.to_numpy_matrix", removedIn: "3.0",
    fix: "networkx.to_numpy_array", fixCode: "nx.to_numpy_array(g)",
    after: ["import networkx as nx", "", "g = nx.Graph([(1, 2), (2, 3)])", "m = nx.to_numpy_array(g)"],
  },
];

// Libraries in the Pinned graph (the GitChameleon 2.0 set ingested from PyPI).
export const LIBRARIES = [
  "pandas", "numpy", "scipy", "scikit-learn", "torch", "matplotlib", "sympy", "networkx", "flask", "django",
  "falcon", "tornado", "librosa", "spacy", "nltk", "pillow", "seaborn", "plotly", "gradio", "lightgbm",
  "geopandas", "jinja2", "pytest", "tqdm", "mitmproxy", "kymatio",
];
