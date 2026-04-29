import { useState } from "react";

export default function PassiveTreeView({ data }) {
  const [hoveredNode, setHoveredNode] = useState(null);

  const nodeMap = Object.fromEntries(data.nodes.map(node => [node.id, node]));

  const minX = Math.min(...data.nodes.map(node => node.x));
  const maxX = Math.max(...data.nodes.map(node => node.x));
  const minY = Math.min(...data.nodes.map(node => node.y));
  const maxY = Math.max(...data.nodes.map(node => node.y));

  const padding = 500;
  const viewBox = `${minX - padding} ${minY - padding} ${maxX - minX + padding * 2} ${maxY - minY + padding * 2}`;

  return (
    <div style={{ width: "100vw", height: "100vh", background: "#111" }}>
      <svg viewBox={viewBox} width="100%" height="100%">
        {data.edges.map(edge => {
          const from = nodeMap[edge.from];
          const to = nodeMap[edge.to];

          if (!from || !to) {
            return null;
          }

          return (
            <line
              key={`${edge.from}-${edge.to}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="#555"
              strokeWidth="12"
            />
          );
        })}

        {data.nodes.map(node => (
          <circle
            key={node.id}
            cx={node.x}
            cy={node.y}
            r="35"
            fill={
              node.classStartIndex !== null
                ? "#4aa3ff"   // class start (blue)
                : node.isNotable
                ? "#d6a84f"   // notable
                : "#ddd"      // normal
            }
            stroke="#222"
            strokeWidth="8"
          >
            <title>
              {node.name + "\n" + (node.stats?.join("\n") ?? "")}
            </title>
          </circle>
        ))}
      </svg>
      {hoveredNode && (
        <div
          style={{
            position: "absolute",
            top: 20,
            left: 20,
            background: "#222",
            color: "#fff",
            padding: "10px",
            borderRadius: "8px",
            maxWidth: "300px",
            pointerEvents: "none"
          }}
        >
          <strong>{hoveredNode.name}</strong>
          <ul>
            {hoveredNode.stats?.map((stat, i) => (
              <li key={i}>{stat}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}