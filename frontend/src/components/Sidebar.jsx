import { useState } from "react";
import "./Sidebar.css";

const AVAILABLE_STATS = [
  "maximum_life",
  "maximum_energy_shield",
  "maximum_mana",
  "attack_speed",
  "cast_speed",
  "fire_resistance",
  "cold_resistance",
  "lightning_resistance",
  "chaos_resistance",
  "physical_damage",
  "fire_damage",
  "cold_damage",
  "lightning_damage",
  "chaos_damage",
  "intelligence",
  "strength",
  "dexterity",
];

const STAT_DISPLAY_NAMES = {
  maximum_life: "Maximum Life",
  maximum_energy_shield: "Maximum Energy Shield",
  maximum_mana: "Maximum Mana",
  attack_speed: "Attack Speed",
  cast_speed: "Cast Speed",
  fire_resistance: "Fire Resistance",
  cold_resistance: "Cold Resistance",
  lightning_resistance: "Lightning Resistance",
  chaos_resistance: "Chaos Resistance",
  physical_damage: "Physical Damage",
  fire_damage: "Fire Damage",
  cold_damage: "Cold Damage",
  lightning_damage: "Lightning Damage",
  chaos_damage: "Chaos Damage",
  intelligence: "Intelligence",
  strength: "Strength",
  dexterity: "Dexterity",
};

const MODIFIER_TYPES = [
  { value: "increased_percent", label: "Increased %" },
  { value: "flat", label: "Flat" },
  { value: "flat_percent", label: "Flat %" },
  { value: "reduced_percent", label: "Reduced %" },
];

const MODIFIER_LABELS = Object.fromEntries(
  MODIFIER_TYPES.map((modifier) => [modifier.value, modifier.label]),
);

const formatStatName = (stat) => STAT_DISPLAY_NAMES[stat] ?? stat.replaceAll("_", " ");

const formatStatValue = (stat) => {
  const suffix = stat.modifier_type.includes("percent") ? "%" : "";
  return `${stat.value.toFixed(1).replace(/\.0$/, "")}${suffix}`;
};

export default function Sidebar({
  allocatedNodes = [],
  classStarts = [],
  selectedClassStartId = "",
  onClassChange,
  onRecommend,
  onClose,
}) {
  const [desiredStats, setDesiredStats] = useState([]);
  const [maxPoints, setMaxPoints] = useState(10);
  const [loading, setLoading] = useState(false);
  const [recommendations, setRecommendations] = useState(null);
  const [selectedRecommendationIndex, setSelectedRecommendationIndex] = useState(0);
  const [error, setError] = useState(null);

  const selectedRecommendation = recommendations?.[selectedRecommendationIndex] ?? null;
  const statsGained = selectedRecommendation?.stats_gained;

  const addStat = () => {
    if (desiredStats.length < AVAILABLE_STATS.length) {
      setDesiredStats([
        ...desiredStats,
        { stat: AVAILABLE_STATS[0], modifierType: MODIFIER_TYPES[0].value, value: 1 },
      ]);
    }
  };

  const removeStat = (index) => {
    setDesiredStats(desiredStats.filter((_, i) => i !== index));
  };

  const updateStat = (index, field, value) => {
    const updated = [...desiredStats];
    updated[index] = { ...updated[index], [field]: value };
    setDesiredStats(updated);
  };

  const getAvailableStats = (currentIndex) => {
    const usedStats = desiredStats
      .map((s, i) => (i === currentIndex ? null : s.stat))
      .filter(Boolean);
    return AVAILABLE_STATS.filter(stat => !usedStats.includes(stat));
  };

  const handleRecommend = async () => {
    if (!selectedClassStartId) {
      setError("Please choose a starting class");
      return;
    }

    if (desiredStats.length === 0) {
      setError("Please add at least one desired stat");
      return;
    }

    setLoading(true);
    setError(null);
    setRecommendations(null);
    setSelectedRecommendationIndex(0);
    onRecommend?.(null);

    try {
      const desired_stats = {};
      desiredStats.forEach(({ stat, modifierType, value }) => {
        const key = [stat, modifierType];
        desired_stats[JSON.stringify(key)] = parseFloat(value) || 0;
      });

      const response = await fetch("/api/recommend-paths", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          allocated: allocatedNodes.map(node => node.toString()),
          max_points: parseInt(maxPoints) || 10,
          desired_stats: desired_stats,
        }),
      });

      if (!response.ok) {
        throw new Error(`API Error: ${response.statusText}`);
      }

      const data = await response.json();
      setRecommendations(data.recommendations);
      setSelectedRecommendationIndex(0);
      if (onRecommend) {
        onRecommend(data.recommendations?.[0] ?? null);
      }
    } catch (err) {
      setError(err.message || "Failed to get recommendations");
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setDesiredStats([]);
    setMaxPoints(10);
    setRecommendations(null);
    setSelectedRecommendationIndex(0);
    setError(null);
    onRecommend?.(null);
  };

  const handleClassChange = (classStartId) => {
    setRecommendations(null);
    setSelectedRecommendationIndex(0);
    setError(null);
    onRecommend?.(null);
    onClassChange?.(classStartId);
  };

  const selectRecommendation = (nextIndex) => {
    if (!recommendations?.length) return;

    const clampedIndex = Math.min(Math.max(nextIndex, 0), recommendations.length - 1);
    setSelectedRecommendationIndex(clampedIndex);
    onRecommend?.(recommendations[clampedIndex]);
  };

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h2>Path Recommendations</h2>
        <button className="btn-close" onClick={onClose}>
          Hide
        </button>
      </div>

      <div className="sidebar-content">
        <div className="section">
          <h3>Starting Class</h3>
          <select
            value={selectedClassStartId}
            onChange={(event) => handleClassChange(event.target.value)}
            className="class-select"
          >
            {classStarts.map((classStart) => (
              <option key={classStart.id} value={classStart.id}>
                {classStart.label}
              </option>
            ))}
          </select>
        </div>

        {/* Allocated Nodes Display */}
        <div className="section">
          <h3>Allocated Nodes</h3>
          <div className="allocated-nodes">
            {allocatedNodes.length > 0 ? (
              <span className="allocated-count">{allocatedNodes.length} nodes</span>
            ) : (
              <span className="allocated-empty">None selected</span>
            )}
          </div>
        </div>

        {/* Max Points Input */}
        <div className="section">
          <h3>Max Points to Spend</h3>
          <input
            type="number"
            min="1"
            max="100"
            value={maxPoints}
            onChange={(e) => setMaxPoints(e.target.value)}
            className="input-number"
          />
        </div>

        {/* Desired Stats */}
        <div className="section">
          <div className="section-header">
            <h3>Desired Stats</h3>
            <button
              onClick={addStat}
              disabled={desiredStats.length >= AVAILABLE_STATS.length}
              className="btn-add"
            >
              + Add
            </button>
          </div>

          <div className="stats-list">
            {desiredStats.length === 0 ? (
              <p className="empty-message">No stats added yet. Click "Add" to get started.</p>
            ) : (
              desiredStats.map((stat, index) => (
                <div key={index} className="stat-row">
                  <select
                    value={stat.stat}
                    onChange={(e) => updateStat(index, "stat", e.target.value)}
                    className="stat-select"
                  >
                    {getAvailableStats(index).map((s) => (
                      <option key={s} value={s}>
                        {STAT_DISPLAY_NAMES[s]}
                      </option>
                    ))}
                  </select>
                  <select
                    value={stat.modifierType}
                    onChange={(e) => updateStat(index, "modifierType", e.target.value)}
                    className="modifier-select"
                  >
                    {MODIFIER_TYPES.map((modifier) => (
                      <option key={modifier.value} value={modifier.value}>
                        {modifier.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    value={stat.value}
                    onChange={(e) => updateStat(index, "value", e.target.value)}
                    placeholder="Target value"
                    className="stat-value"
                  />
                  <button
                    onClick={() => removeStat(index)}
                    className="btn-remove"
                    title="Remove stat"
                    aria-label={`Remove ${STAT_DISPLAY_NAMES[stat.stat]}`}
                  >
                    x
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Error Display */}
        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        {/* Recommendations Display */}
        {recommendations && (
          <div className="section recommendations">
            <h3>Recommendations</h3>
            {recommendations.length > 0 ? (
              <>
                <div className="recommendation-controls">
                  <button
                    type="button"
                    onClick={() => selectRecommendation(selectedRecommendationIndex - 1)}
                    disabled={selectedRecommendationIndex === 0}
                    className="btn-step"
                  >
                    Prev
                  </button>
                  <span className="recommendation-counter">
                    {selectedRecommendationIndex + 1} / {recommendations.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => selectRecommendation(selectedRecommendationIndex + 1)}
                    disabled={selectedRecommendationIndex === recommendations.length - 1}
                    className="btn-step"
                  >
                    Next
                  </button>
                </div>

                <div className="recommendation-summary">
                  <div>
                    <span>Target</span>
                    <strong>{selectedRecommendation?.target}</strong>
                  </div>
                  <div>
                    <span>Cost</span>
                    <strong>{selectedRecommendation?.cost}</strong>
                  </div>
                  <div>
                    <span>Score</span>
                    <strong>{selectedRecommendation?.score?.toFixed(1)}</strong>
                  </div>
                  <div>
                    <span>Efficiency</span>
                    <strong>{selectedRecommendation?.efficiency?.toFixed(2)}</strong>
                  </div>
                </div>

                <div className="stat-gains">
                  <div className="stat-gain-group">
                    <h4>Desired Stats Gained</h4>
                    {!statsGained ? (
                      <p>Stat breakdown unavailable from the backend.</p>
                    ) : statsGained.desired?.length > 0 ? (
                      <ul>
                        {statsGained.desired.map((stat) => (
                          <li key={`${stat.stat_type}-${stat.modifier_type}`}>
                            <span>
                              {formatStatName(stat.stat_type)}
                              <small>{MODIFIER_LABELS[stat.modifier_type] ?? stat.modifier_type}</small>
                            </span>
                            <strong>{formatStatValue(stat)}</strong>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>No desired stats gained.</p>
                    )}
                  </div>

                  <div className="stat-gain-group">
                    <h4>Other Stats Gained</h4>
                    {!statsGained ? (
                      <p>Stat breakdown unavailable from the backend.</p>
                    ) : statsGained.other?.length > 0 ? (
                      <ul>
                        {statsGained.other.map((stat) => (
                          <li key={`${stat.stat_type}-${stat.modifier_type}`}>
                            <span>
                              {formatStatName(stat.stat_type)}
                              <small>{MODIFIER_LABELS[stat.modifier_type] ?? stat.modifier_type}</small>
                            </span>
                            <strong>{formatStatValue(stat)}</strong>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>No other parsed stats gained.</p>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <p className="empty-message">No matching paths found.</p>
            )}
          </div>
        )}

        {/* Action Buttons */}
        <div className="button-group">
          <button
            onClick={handleRecommend}
            disabled={loading || desiredStats.length === 0 || !selectedClassStartId}
            className="btn-recommend"
          >
            {loading ? "Loading..." : "Get Recommendations"}
          </button>
          <button
            onClick={handleClear}
            className="btn-clear"
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}

