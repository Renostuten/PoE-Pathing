// src/App.jsx

import { useEffect, useState } from "react";
import PassiveTreeView from "./components/PassiveTreeView";

export default function App() {
  const [treeData, setTreeData] = useState(null);

  useEffect(() => {
    fetch("/tree-graph.json")
      .then(response => response.json())
      .then(data => setTreeData(data));
  }, []);

  if (!treeData) {
    return <p>Loading tree...</p>;
  }

  return <PassiveTreeView data={treeData} />;
}