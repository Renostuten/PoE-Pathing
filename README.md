# PoE-Pathing
A tool to find the most optimal (shortest) pathing from a specific node in the passive tree to hit desired stats. The first final goal of this tool is to be able to choose a skill/spell gem and have the passive tree mapped out for you based off of the chosen ability.

**Current stage:**
- Frontend
    - Frontend can display the current poe tree layout
    - Hovering over tree nodes in Frontend shows node data
- Backend
    - Basic pathfinding algorithms implemented
    - Basic node/path scoring mechanisms implemented

**TODOs in order:**
- Create frontend interface that allows the user to choose desired stats and class
- Display recommended paths on the frontend tree based off of the desired stats and class
- Create frontend interface that displays a breakdown on the recommendations
    - Point cost
    - Stats gained (both desired and non-desired)
- Add build presets so user does not need to manually choose every stat
    - Fire/other elemental spell caster
    - Minion build
    - Weapon specific build
    - Levelling build
- Add gem profiles
    - Break skill/spell gems down into their tags (attack/spell, projectile/melee, fire, etc.)
    - Define what each gem can scale well with
- Connect stat scoring to gem profiles
    - Extract desired stats from gem tags and what gem scales with
- Optimise logic to build around clusters rather than specific nodes
    - Clusters contain many desired nodes so this could be more point efficient or provide greater desired stat value instead of going by individual nodes
    - Can perhaps add mastery nodes back in
- Generate the tree in stages as final stage may not be the most optimal on when points are limited
    - Split this into levelling stages (first 20, 40, 70 points and then 90+ points for endgame)
- Add ascendancy trees into the build planner
    - Perhaps look into all ascendancies of a chosen class and provide a breakdown on what each ascendancy can provide for the build (pros/cons)
- Add stat constraints
    - Optional Minimum + Maximum value for each stat

**Direct next step:**
- Create frontend interface that allows the user to choose desired stats and class

**TODOs after main list is complete**
- Add jewels/cluster jewels into the build planner
- Bloodline ascendancies
- Web search tool to look at already existing builds for chosen gems
- AI model/AI tool to help with all build planning stages and provide breakdown for each stage
- Recommend support gems to chosen active skill gems
    - Perhaps look into adding secondary active gems
    - Aura gems
- Character gearing once all tree and gem logic is complete
- Incorporation with PoB and extraction to a .build file once new PoE1/2 update releases