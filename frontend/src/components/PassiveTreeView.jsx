export default function PassiveTreeView({ data }) {
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
            fill={node.isNotable ? "#d6a84f" : "#ddd"}
            stroke="#222"
            strokeWidth="8"
          >
            <title>{node.name}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}