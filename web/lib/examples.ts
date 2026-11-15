// Well-known, documented API changes used as on-page examples (each one is a real removal in that release).
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
    before: ["import pandas as pd", "", "def add_row(df, row):", "    return df.append(row, ignore_index=True)"],
    bad: "df.append", call: "pandas.DataFrame.append", removedIn: "2.0.0",
    fix: "pandas.concat", fixCode: "pd.concat([df, pd.DataFrame([row])], ignore_index=True)",
    after: ["import pandas as pd", "", "def add_row(df, row):", "    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)"],
  },
  {
    lib: "numpy", version: "1.26.4", file: "stats.py",
    before: ["import numpy as np", "", "def mean_ratio(a, b):", "    return np.float(a.sum()) / b.sum()"],
    bad: "np.float", call: "numpy.float", removedIn: "1.24.0",
    fix: "builtins.float", fixCode: "float(a.sum())",
    after: ["import numpy as np", "", "def mean_ratio(a, b):", "    return float(a.sum()) / b.sum()"],
  },
  {
    lib: "scikit-learn", version: "1.3.2", file: "train.py",
    before: ["from sklearn.datasets import load_boston", "", "X, y = load_boston(return_X_y=True)"],
    bad: "load_boston", call: "sklearn.datasets.load_boston", removedIn: "1.2.0",
    fix: "sklearn.datasets.fetch_california_housing", fixCode: "fetch_california_housing(return_X_y=True)",
    after: ["from sklearn.datasets import fetch_california_housing", "", "X, y = fetch_california_housing(return_X_y=True)"],
  },
  {
    lib: "networkx", version: "3.2.1", file: "graph.py",
    before: ["import networkx as nx", "", "def adjacency(g):", "    return nx.to_numpy_matrix(g)"],
    bad: "nx.to_numpy_matrix", call: "networkx.to_numpy_matrix", removedIn: "3.0",
    fix: "networkx.to_numpy_array", fixCode: "nx.to_numpy_array(g)",
    after: ["import networkx as nx", "", "def adjacency(g):", "    return nx.to_numpy_array(g)"],
  },
];

// Libraries in the Pinned graph (the GitChameleon 2.0 set ingested from PyPI).
export const LIBRARIES = [
  "pandas", "numpy", "scipy", "scikit-learn", "torch", "matplotlib", "sympy", "networkx", "flask", "django",
  "falcon", "tornado", "librosa", "spacy", "nltk", "pillow", "seaborn", "plotly", "gradio", "lightgbm",
  "geopandas", "jinja2", "pytest", "tqdm", "mitmproxy", "kymatio",
];
