import { useMemo, useRef, useState } from "react";

export default function PassiveTreeView({
  data,
  allocatedNodes = [],
  selectedRecommendation = null,
  onNodeClick = null,
  sidebarOpen = true,
  onToggleSidebar = null,
}) {
  const svgRef = useRef(null);
  const dragDistanceRef = useRef(0);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState(null);
  const [panViewBox, setPanViewBox] = useState(null);

  const nodeMap = Object.fromEntries(data.nodes.map((node) => [node.id, node]));
  const allocatedIds = useMemo(
    () => new Set(allocatedNodes.map(String)),
    [allocatedNodes],
  );

  const initialViewBox = useMemo(() => {
    const minX = Math.min(...data.nodes.map((node) => node.x));
    const maxX = Math.max(...data.nodes.map((node) => node.x));
    const minY = Math.min(...data.nodes.map((node) => node.y));
    const maxY = Math.max(...data.nodes.map((node) => node.y));
    const padding = 500;

    return {
      x: minX - padding,
      y: minY - padding,
      width: maxX - minX + padding * 2,
      height: maxY - minY + padding * 2,
    };
  }, [data.nodes]);

  const [viewBox, setViewBox] = useState(initialViewBox);

  const getNodeRadius = (node) => {
    if (node.isKeystone) {
      return 60;
    }
    if (node.isNotable) {
      return 45;
    }
    return 35;
  };

  const getNodeColor = (node) => {
    if (allocatedIds.has(String(node.id))) {
      return "#22ff22";
    }

    if (node.classStartIndex !== null) {
      return "#4aa3ff";
    }
    if (node.isNotable) {
      return "#d6a84f";
    }
    if (node.isKeystone) {
      return "#ff4a4a";
    }
    return "#ddd";
  };

  const getSvgPoint = (event) => {
    const svg = svgRef.current;
    if (!svg) return null;

    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;

    return point.matrixTransform(svg.getScreenCTM().inverse());
  };

  const clampZoomSize = (width, height) => {
    const minWidth = initialViewBox.width * 0.3;
    const maxWidth = initialViewBox.width * 3.5;
    const clampedWidth = Math.min(Math.max(width, minWidth), maxWidth);
    const clampedHeight = height * (clampedWidth / width);

    return { width: clampedWidth, height: clampedHeight };
  };

  const handleWheel = (event) => {
    event.preventDefault();
    const svgPoint = getSvgPoint(event);
    if (!svgPoint) return;

    const zoomDelta = -event.deltaY * 0.001;
    const zoomFactor = Math.min(Math.max(1 + zoomDelta, 0.75), 1.25);

    const unclampedWidth = viewBox.width / zoomFactor;
    const unclampedHeight = viewBox.height / zoomFactor;
    const { width: newWidth, height: newHeight } = clampZoomSize(unclampedWidth, unclampedHeight);

    if (newWidth === viewBox.width && newHeight === viewBox.height) {
      return;
    }

    const newX = svgPoint.x - ((svgPoint.x - viewBox.x) / viewBox.width) * newWidth;
    const newY = svgPoint.y - ((svgPoint.y - viewBox.y) / viewBox.height) * newHeight;

    setViewBox({ x: newX, y: newY, width: newWidth, height: newHeight });
  };

  const handlePointerDown = (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) {
      return;
    }

    const svg = svgRef.current;
    if (!svg) return;

    setIsPanning(true);
    dragDistanceRef.current = 0;
    setPanStart({ x: event.clientX, y: event.clientY });
    setPanViewBox(viewBox);
    svg.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event) => {
    if (!isPanning || !panStart || !panViewBox) return;
    const svg = svgRef.current;
    if (!svg) return;

    const rect = svg.getBoundingClientRect();
    const dx = event.clientX - panStart.x;
    const dy = event.clientY - panStart.y;
    dragDistanceRef.current = Math.max(dragDistanceRef.current, Math.hypot(dx, dy));
    const scaleX = panViewBox.width / rect.width;
    const scaleY = panViewBox.height / rect.height;

    setViewBox({
      x: panViewBox.x - dx * scaleX,
      y: panViewBox.y - dy * scaleY,
      width: panViewBox.width,
      height: panViewBox.height,
    });
  };

  const stopPan = () => {
    setIsPanning(false);
    setPanStart(null);
    setPanViewBox(null);
  };

  const handleNodeClick = (event, nodeId) => {
    event.stopPropagation();

    if (dragDistanceRef.current < 6 && onNodeClick) {
      onNodeClick(nodeId);
    }
  };

  const recommendedIds = useMemo(() => {
    if (!selectedRecommendation) return new Set();

    if (Array.isArray(selectedRecommendation)) {
      return new Set(selectedRecommendation.map(String));
    }

    if (selectedRecommendation.path) {
      return new Set(selectedRecommendation.path.map(String));
    }

    if (selectedRecommendation.target) {
      return new Set([String(selectedRecommendation.target)]);
    }

    return new Set([String(selectedRecommendation)]);
  }, [selectedRecommendation]);

  const recommendationTargetId = selectedRecommendation?.target ? String(selectedRecommendation.target) : null;

  return (
    <div className="tree-view-shell" style={{ width: "100%", height: "100%", position: "relative", background: "#111" }}>
      <svg
        ref={svgRef}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
        width="100%"
        height="100%"
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={stopPan}
        onPointerCancel={stopPan}
        onDoubleClick={() => setViewBox(initialViewBox)}
        style={{ touchAction: "none", cursor: isPanning ? "grabbing" : "grab" }}
      >
        {data.edges.map((edge) => {
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

        {data.nodes.map((node) => (
          <circle
            key={node.id}
            cx={node.x}
            cy={node.y}
            r={getNodeRadius(node)}
            fill={getNodeColor(node)}
            stroke={recommendedIds.has(String(node.id)) ? "#5fdaff" : allocatedIds.has(String(node.id)) ? "#ffff00" : "#222"}
            strokeWidth={String(node.id) === recommendationTargetId ? "18" : recommendedIds.has(String(node.id)) ? "14" : allocatedIds.has(String(node.id)) ? "12" : "8"}
            style={{ cursor: "pointer", transition: "all 0.1s ease" }}
            onMouseEnter={() => setHoveredNode(node)}
            onMouseLeave={() => setHoveredNode(null)}
            onClick={(event) => handleNodeClick(event, node.id)}
          >
            <title>{[node.name, ...(node.stats ?? [])].join("\n")}</title>
          </circle>
        ))}
      </svg>

      {!sidebarOpen && onToggleSidebar && (
        <button className="sidebar-toggle sidebar-open-mobile" onClick={onToggleSidebar}>
          Open Sidebar
        </button>
      )}

      {hoveredNode && (
        <div
          style={{
            position: "absolute",
            top: sidebarOpen ? 20 : 74,
            left: 20,
            background: "#222",
            color: "#fff",
            padding: "10px",
            borderRadius: "8px",
            maxWidth: "300px",
            pointerEvents: "none",
            border: "2px solid #4aa3ff",
          }}
        >
          <strong>{hoveredNode.name}</strong>
          <ul style={{ marginTop: "8px", paddingLeft: "20px" }}>
            {hoveredNode.stats?.map((stat, i) => (
              <li key={i} style={{ fontSize: "12px", color: "#ccc" }}>
                {stat}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
