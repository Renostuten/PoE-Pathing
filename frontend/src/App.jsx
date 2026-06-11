// src/App.jsx

import { useEffect, useMemo, useState } from "react";
import PassiveTreeView from "./components/PassiveTreeView";
import Sidebar from "./components/Sidebar";
import "./App.css";

const CLASS_NAMES_BY_INDEX = {
  0: "Scion",
  1: "Marauder",
  2: "Ranger",
  3: "Witch",
  4: "Duelist",
  5: "Templar",
  6: "Shadow",
};

export default function App() {
  const [treeData, setTreeData] = useState(null);
  const [allocatedNodes, setAllocatedNodes] = useState([]);
  const [selectedRecommendation, setSelectedRecommendation] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedClassStartId, setSelectedClassStartId] = useState("");

  useEffect(() => {
    fetch("/tree-graph.json")
      .then((response) => response.json())
      .then((data) => {
        setTreeData(data);

        const defaultStart = data.nodes.find((node) => node.classStartIndex === 0);
        if (defaultStart) {
          setSelectedClassStartId(defaultStart.id);
          setAllocatedNodes([defaultStart.id]);
        }
      });
  }, []);

  const adjacency = useMemo(() => {
    if (!treeData) return new Map();

    const nextAdjacency = new Map();
    treeData.edges.forEach(({ from, to }) => {
      const fromId = String(from);
      const toId = String(to);

      if (!nextAdjacency.has(fromId)) nextAdjacency.set(fromId, new Set());
      if (!nextAdjacency.has(toId)) nextAdjacency.set(toId, new Set());

      nextAdjacency.get(fromId).add(toId);
      nextAdjacency.get(toId).add(fromId);
    });

    return nextAdjacency;
  }, [treeData]);

  const classStarts = useMemo(() => {
    if (!treeData) return [];

    return treeData.nodes
      .filter((node) => node.classStartIndex !== null)
      .sort((a, b) => a.classStartIndex - b.classStartIndex)
      .map((node) => ({
        id: node.id,
        label: CLASS_NAMES_BY_INDEX[node.classStartIndex] ?? node.name,
      }));
  }, [treeData]);

  const canAllocateNode = (nodeId, allocatedSet) => {
    if (!selectedClassStartId) return false;
    if (nodeId === selectedClassStartId) return false;

    const neighbours = adjacency.get(nodeId);
    if (!neighbours) return false;

    return [...neighbours].some((neighbourId) => allocatedSet.has(neighbourId));
  };

  const remainsConnectedToStart = (nextAllocated) => {
    if (!selectedClassStartId) return false;

    const nextAllocatedSet = new Set(nextAllocated);
    const queue = [selectedClassStartId];
    const visited = new Set();

    while (queue.length > 0) {
      const current = queue.shift();
      if (visited.has(current)) continue;
      visited.add(current);

      adjacency.get(current)?.forEach((neighbourId) => {
        if (nextAllocatedSet.has(neighbourId) && !visited.has(neighbourId)) {
          queue.push(neighbourId);
        }
      });
    }

    return nextAllocated.every((nodeId) => visited.has(nodeId));
  };

  const handleClassChange = (classStartId) => {
    setSelectedClassStartId(classStartId);
    setAllocatedNodes(classStartId ? [classStartId] : []);
    setSelectedRecommendation(null);
  };

  const handleNodeClick = (nodeId) => {
    const nodeIdString = String(nodeId);

    setAllocatedNodes((prev) => {
      const allocatedSet = new Set(prev);

      if (allocatedSet.has(nodeIdString)) {
        if (nodeIdString === selectedClassStartId) {
          return prev;
        }

        const nextAllocated = prev.filter((id) => id !== nodeIdString);
        return remainsConnectedToStart(nextAllocated) ? nextAllocated : prev;
      }

      if (!canAllocateNode(nodeIdString, allocatedSet)) {
        return prev;
      }

      return [...prev, nodeIdString];
    });
  };

  const handleRecommend = (recommendation) => {
    setSelectedRecommendation(recommendation);
  };

  const toggleSidebar = () => {
    setSidebarOpen((prev) => !prev);
  };

  if (!treeData) {
    return <p className="loading-state">Loading tree...</p>;
  }

  return (
    <div className="app-container">
      {sidebarOpen && (
        <Sidebar
          allocatedNodes={allocatedNodes}
          classStarts={classStarts}
          selectedClassStartId={selectedClassStartId}
          onClassChange={handleClassChange}
          onRecommend={handleRecommend}
          onClose={toggleSidebar}
        />
      )}

      <div className="tree-wrap">
        <PassiveTreeView
          data={treeData}
          allocatedNodes={allocatedNodes}
          selectedRecommendation={selectedRecommendation}
          onNodeClick={handleNodeClick}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={toggleSidebar}
        />
      </div>
    </div>
  );
}
