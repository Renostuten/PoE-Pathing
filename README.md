# PoE-Pathing

Passive tree recommendation and build planning tool for Path of Exile.

## Project Goal

The long-term goal of this project is to automatically generate passive tree progression based on:

- Starting class
- Skill/spell gem
- Desired stats
- Build archetype
- Constraints

The system should eventually generate complete levelling and endgame tree recommendations.

## Progress Breakdown Reference
- [ ] = TODO
- [x] = Completed
- 🚧 = Work in progress

## Current Progress

### Frontend
- [x] Frontend can display the current poe tree layout
- [x] Hovering over tree nodes in Frontend shows node data

### Backend
- [x] Basic pathfinding algorithms implemented
- [x] Basic node/path scoring mechanisms implemented

## Core Roadmap:

### Phase 1 - Recommendation System
- 🚧 Create frontend interface that allows the user to choose desired stats and class
- [ ] Display recommended paths on the frontend tree based off of the desired stats and class
- [ ] Create frontend interface that displays a breakdown on the recommendations
    - Point cost
    - Stats gained (both desired and non-desired)

### Phase 2 - Build Planner Intelligence
- [ ] Add build presets so user does not need to manually choose every stat
    - Fire/other elemental spell caster
    - Minion build
    - Weapon specific build
    - Levelling build
- [ ] Add gem profiles
    - Break skill/spell gems down into their tags (attack/spell, projectile/melee, fire, etc.)
    - Define what each gem can scale well with
- [ ] Connect stat scoring to gem profiles
    - Extract desired stats from gem tags and what gem scales with

### Phase 3 - Tree Optimisation
- [ ] Optimise logic to build around clusters rather than specific nodes
    - Clusters contain many desired nodes so this could be more point efficient or provide greater desired stat value instead of going by individual nodes
    - Can perhaps add mastery nodes back in
- [ ] Generate the tree in stages as final stage may not be the most optimal on when points are limited
    - Split this into levelling stages (first 20, 40, 70 points and then 90+ points for endgame)
- [ ] Add ascendancy trees into the build planner
    - Perhaps look into all ascendancies of a chosen class and provide a breakdown on what each ascendancy can provide for the build (pros/cons)
- [ ] Add stat constraints
    - Optional Minimum + Maximum value for each stat

### Phase 4 - Full Build Planner
- [ ] Add jewels/cluster jewels into the build planner
- [ ] Bloodline ascendancies incorporation
- [ ] Recommend support gems to chosen active skill gems
    - Perhaps look into adding secondary active gems
    - Aura gems
- [ ] Incorporation with PoB and extraction to a .build file once new PoE1/2 update releases

## Possible Future Ideas
- Web search tool to look at already existing builds for chosen gems
- AI model/AI tool to help with all build planning stages and provide breakdown for each stage
- Character gearing once all tree and gem logic is complete
