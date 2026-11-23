#!/usr/bin/env python3
"""Convert a Maister Claude Code plugin checkout (or ZIP) to OpenCode 1.19.0 with maister- prefixed agent/skill IDs.

By default, the converter searches the repository containing this script for the Claude plugin (normally
plugins/maister/). It can also read a repository directory, a plugin directory, or a legacy ZIP archive.
The converter does not execute source scripts. It converts Claude Code slash commands to native OpenCode
commands, maps known Claude-only workflow tools to OpenCode tools/instructions, preserves skill resources,
generates a global plugin and
installer, and fails static validation when known Claude-only tool references remain in active output.
Static validation is not a runtime guarantee; test the bundle before relying on it.
"""
from __future__ import annotations
import argparse, json, re, sys, zipfile
from pathlib import Path, PurePosixPath

VERSION = "OpenCode 1.19.0 target"
FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
SKILL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".html", ".css", ".js", ".ts", ".py", ".sh", ".xml", ".svg", ".toml"}
CLAUDE_TOOL_TOKENS = ["AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "EnterPlanMode", "ExitPlanMode"]

PLUGIN_TS = r'''/**
 * Maister compatibility hooks for OpenCode 1.19.x (V1 plugin API).
 * The shell guard is pattern-based and is NOT a security boundary.
 */
export const MaisterCompatibility = async () => ({
  "experimental.chat.system.transform": async (_input, output) => {
    output.system.push(`MAISTER / OPENCODE COMPATIBILITY
Use only tools actually available in this OpenCode session. For questions requiring structured choices, use the OpenCode question tool when available; otherwise ask the user directly in chat and wait for an explicit answer. Never bypass a mandatory approval gate.
Use the OpenCode skill tool with the exact installed skill name from the available skill list. Do not invent skill names. Use the OpenCode task tool with an exact installed subagent name when delegation is needed; never claim a delegation happened unless the task tool actually ran.
OpenCode todowrite replaces the entire todo list: send the complete current list when creating or updating it. Keep dependency edges and workflow phase state in the Maister state file because native todos do not model arbitrary blocked-by relationships.
If plan mode or plan_exit is unavailable, write the plan in chat and wait for explicit user approval before implementation. If a source Maister step cannot be represented, report that limitation rather than silently skipping it.`)
  },
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return
    const command = typeof output.args?.command === "string" ? output.args.command : ""
    if (!command) return
    const blocked = [
      /\bgit\s+reset\s+--hard\b/i,
      /\bgit\s+clean\b/i,
      /\bgit\s+stash\b/i,
      /\bgit\s+push\b[^\n]*(?:\s-f(?:\s|$)|\s--force(?:\s|$))/i,
      /\brm\s+(?:-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)(?:\s|$)/i,
      /\brm\s+--(?:recursive|force)(?:\s|$)/i,
    ]
    if (blocked.some((pattern) => pattern.test(command))) {
      throw new Error("Maister guard blocked a potentially destructive shell command. Review it and use OpenCode permissions. This pattern-based guard is not a security boundary.")
    }
  },
  "experimental.session.compacting": async (_input, output) => {
    output.context.push(`MAISTER WORKFLOW CONTINUITY
If a Maister workflow is active, preserve its current workflow and phase, completed and pending phases, acceptance criteria, decisions awaiting user input, important decisions, files in progress, dependency edges, and exact path to orchestrator-state.yml. After compaction, inspect the actual state file before resuming. Do not claim a skill, test, tool, or delegated agent ran unless it actually did.`)
  },
})
export default MaisterCompatibility
'''

def safe_name(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and not name.startswith(("/", "\\"))

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = FM_RE.match(text)
    if not m: return {}, text
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if line and not line[0].isspace() and ":" in line:
            k, v = line.split(":", 1); k = k.strip(); v = v.strip()
            if v.startswith(('"', "'")) and len(v) > 1 and v[-1] == v[0]: v = v[1:-1]
            fields[k] = v
    return fields, text[m.end():]

def yaml_quote(s: str) -> str:
    return json.dumps(s.replace("\r", " ").replace("\n", " "), ensure_ascii=False)

def skill_slug(source_name: str, directory: str) -> str:
    candidate = (source_name or directory).strip()
    if candidate.startswith("maister:"): candidate = candidate.split(":", 1)[1]
    candidate = candidate.lower()
    if not SKILL_RE.fullmatch(candidate): candidate = directory.lower()
    if not SKILL_RE.fullmatch(candidate): raise ValueError(f"Cannot convert skill name {source_name!r} / directory {directory!r} to OpenCode kebab-case")
    if not candidate.startswith("maister-"):
        candidate = "maister-" + candidate
    if not SKILL_RE.fullmatch(candidate): raise ValueError(f"Invalid namespaced skill name: {candidate!r}")
    return candidate

def adapt_skill_references(text: str, skill_dirs: dict[str, str]) -> str:
    """Rename explicit skill references without rewriting ordinary prose using common words."""
    for old, new in sorted(skill_dirs.items(), key=lambda item: -len(item[0])):
        if old == new.removeprefix("maister-") and new == "maister-" + old:
            pass
        # Explicit identifiers in code spans, paths, tool arguments and workflow labels.
        text = re.sub(r"(?<![A-Za-z0-9_-])`" + re.escape(old) + r"`(?![A-Za-z0-9_-])", "`" + new + "`", text)
        text = re.sub(r"(?i)(?<=skills/)" + re.escape(old) + r"(?=/|\b)", new, text)
        text = re.sub(r"(?i)(?<![A-Za-z0-9_-])(?:skill(?:_name|_id)?\s*[:=]\s*[\"'`]?|(?:invoke|load|call|use)\s+(?:the\s+)?(?:skill\s+)?[\"'`])" + re.escape(old) + r"(?![A-Za-z0-9_-])",
                      lambda m: m.group(0)[:-len(old)] + new, text)
        # Common explicit Maister workflow references such as "the development (Phase 4) orchestrator".
        text = re.sub(r"(?i)(?<![A-Za-z0-9_-])the\s+" + re.escape(old) + r"(?=\s+\((?:Phase|Step)\b)", "the " + new, text)
        text = re.sub(r"(?i)(?<![A-Za-z0-9_-])" + re.escape(old) + r"(?=\s+(?:skill|orchestrator)\b)", new, text)
    return text

def find_root(z) -> str:
    """Resolve the Claude Code Maister plugin, not the Copilot-generated sibling."""
    names = [n for n in z.namelist() if safe_name(n) and not n.endswith("/")]

    def has_agent_and_skill(root: str) -> bool:
        return (
            any(n.startswith(root + "agents/") and n.endswith(".md") for n in names)
            and any(n.startswith(root + "skills/") and n.endswith("/SKILL.md") for n in names)
        )

    def normalize_root(value: str) -> str:
        value = value.replace("\\", "/").strip()
        while value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe plugin source path in marketplace manifest: {value!r}")
        return "" if str(path) in ("", ".") else path.as_posix().rstrip("/") + "/"

    # The repository root marketplace manifest is authoritative. It deliberately
    # declares both `maister` (Claude Code) and `maister-copilot` (Copilot CLI).
    marketplace_path = ".claude-plugin/marketplace.json"
    if marketplace_path in names:
        try:
            marketplace = json.loads(z.read(marketplace_path).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Cannot parse {marketplace_path}: {exc}") from exc

        entries = marketplace.get("plugins", [])
        matches = [
            entry for entry in entries
            if isinstance(entry, dict) and str(entry.get("name", "")).lower() == "maister"
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{marketplace_path} must declare exactly one plugin named 'maister'; "
                f"found {len(matches)}"
            )
        source_path = matches[0].get("source")
        if not isinstance(source_path, str) or not source_path.strip():
            raise ValueError("Marketplace entry 'maister' has no valid 'source' path")
        root = normalize_root(source_path)
        plugin_manifest = root + ".claude-plugin/plugin.json"
        if plugin_manifest not in names:
            raise ValueError(
                f"Marketplace points to {root!r}, but {plugin_manifest} is missing"
            )
        try:
            plugin_meta = json.loads(z.read(plugin_manifest).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Cannot parse {plugin_manifest}: {exc}") from exc
        if str(plugin_meta.get("name", "")).lower() != "maister":
            raise ValueError(
                f"Marketplace entry 'maister' points to a plugin whose manifest name is "
                f"{plugin_meta.get('name')!r}, expected 'maister'"
            )
        if not has_agent_and_skill(root):
            raise ValueError(
                f"Selected Claude plugin at {root!r} does not contain both agents/ and skills/"
            )
        return root

    # When the user passes plugins/maister directly (or a standalone ZIP),
    # select the plugin manifest whose declared name is exactly "maister".
    manifest_candidates = []
    for name in names:
        p = PurePosixPath(name)
        if p.name != "plugin.json" or p.parent.name != ".claude-plugin":
            continue
        root_path = p.parent.parent
        root = "" if str(root_path) == "." else root_path.as_posix().rstrip("/") + "/"
        try:
            metadata = json.loads(z.read(name).decode("utf-8"))
        except Exception:
            continue
        if str(metadata.get("name", "")).lower() == "maister" and has_agent_and_skill(root):
            manifest_candidates.append(root)

    manifest_candidates = sorted(set(manifest_candidates))
    if len(manifest_candidates) == 1:
        return manifest_candidates[0]
    if len(manifest_candidates) > 1:
        raise ValueError(
            "Found multiple plugin manifests named 'maister': "
            f"{manifest_candidates}. Pass the exact plugins/maister directory."
        )

    # Last-resort compatibility for old standalone bundles without plugin.json.
    roots = sorted({
        (
            "" if PurePosixPath(n).parent.parent.as_posix() == "."
            else PurePosixPath(n).parent.parent.as_posix().rstrip("/") + "/"
        )
        for n in names if "/agents/" in n and n.endswith(".md")
    })
    valid = [
        root for root in roots
        if has_agent_and_skill(root)
    ]
    if len(valid) != 1:
        raise ValueError(
            "Could not identify exactly one Maister Claude Code plugin. "
            f"Candidate roots: {valid or roots}. For this repository, run the script "
            "from the repository root so it can read .claude-plugin/marketplace.json, "
            "or pass plugins/maister explicitly."
        )
    return valid[0]


class DirectorySource:
    """Read a repository checkout using the same interface as ZipFile."""

    EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}

    def __init__(self, base: Path):
        self.base = base.resolve()

    def __enter__(self):
        if not self.base.is_dir():
            raise FileNotFoundError(f"Source directory not found: {self.base}")
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def namelist(self):
        names = []
        for path in self.base.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.base)
            if any(part in self.EXCLUDED_DIRS for part in relative.parts):
                continue
            names.append(relative.as_posix())
        return sorted(names)

    def read(self, name: str) -> bytes:
        if not safe_name(name):
            raise ValueError(f"Unsafe source path: {name}")
        path = (self.base / Path(*PurePosixPath(name).parts)).resolve()
        if self.base != path and self.base not in path.parents:
            raise ValueError(f"Source path escapes checkout: {name}")
        if not path.is_file():
            raise FileNotFoundError(f"Source file not found: {path}")
        return path.read_bytes()

    def testzip(self):
        return None


def open_source(source: Path | None):
    """Return (reader, source description); default to this script's repository."""
    if source is None:
        base = Path(__file__).resolve().parent
        return DirectorySource(base), f"repository checkout: {base}"
    source = source.expanduser().resolve()
    if source.is_dir():
        return DirectorySource(source), f"directory checkout: {source}"
    if source.is_file() and source.suffix.lower() == ".zip":
        return zipfile.ZipFile(source), f"ZIP archive: {source}"
    raise FileNotFoundError(
        f"Source must be a repository/plugin directory or a .zip archive: {source}"
    )

def convert_mcp(raw: bytes) -> dict:
    source = json.loads(raw.decode("utf-8"))
    servers = source.get("mcpServers", {})
    if not isinstance(servers, dict): raise ValueError(".mcp.json mcpServers must be an object")
    out = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("command"), str):
            raise ValueError(f"Unsupported MCP definition for {name!r}; expected command-based server")
        command = [cfg["command"], *[str(a) for a in cfg.get("args", [])]]
        entry = {"type": "local", "command": command, "enabled": True}
        if isinstance(cfg.get("env"), dict): entry["environment"] = cfg["env"]
        if isinstance(cfg.get("timeout"), (int, float)): entry["timeout"] = cfg["timeout"]
        out[name] = entry
    return out

def adapt_text(text: str, skill_map: dict[str, str], agent_names: set[str], warnings: list[str], where: str, command_map: dict[str, str] | None = None) -> str:
    # Special case first: Claude task dependency API has no direct OpenCode todo equivalent.
    text = re.sub(r"\bTaskUpdate\s+addBlockedBy\b", "record dependency edges in orchestrator-state.yml and refresh the full OpenCode todo list using todowrite", text)
    text = re.sub(r"Call `?TaskList`? to find existing task group items from the planner\. If found, use them\. If not, create them with `?TaskCreate`? for each task group \(when the planner created none\)\.", "Check orchestrator-state.yml and implementation-plan.md for existing task-group items. If none exist, create all task-group todos in one full-list todowrite call.", text)
    text = re.sub(r"All group tasks are `?completed`? via `?TaskList`? \(cross-validate against markdown checkboxes\)\.", "Verify all group tasks against orchestrator-state.yml and markdown checkboxes.", text)
    # Convert Claude slash-command references to native OpenCode command IDs,
    # or to skill/agent instructions when the source reference is not a command.
    command_map = command_map or {}
    command_re = re.compile(r"(?<!\w)/maister:([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*)")
    def command_sub(m):
        name = m.group(1).lower()
        if name in command_map:
            return f"/{command_map[name]}"
        if "maister:" + name in command_map:
            return f"/{command_map['maister:' + name]}"
        if name in skill_map:
            target = skill_map[name]
            return f"the OpenCode skill `{target}` (load it with the `skill` tool)"
        if "maister:" + name in skill_map:
            target = skill_map["maister:" + name]
            return f"the OpenCode skill `{target}` (load it with the `skill` tool)"
        if name in skill_map.values(): return f"the OpenCode skill `{name}` (load it with the `skill` tool)"
        if name in agent_names: return f"the OpenCode `maister-{name}` subagent (delegate with the `task` tool)"
        warnings.append(f"{where}: source slash command /maister:{name} has no matching converted command, skill, or agent")
        return f"unresolved Maister command '{name}'"
    text = command_re.sub(command_sub, text)

    # The command's own examples commonly use the un-namespaced Claude alias, e.g. /work.
    # Rewrite only known command aliases, not arbitrary filesystem paths.
    for alias, target in sorted(command_map.items(), key=lambda item: -len(item[0])):
        if ":" in alias or alias.startswith("maister-"):
            continue
        text = re.sub(
            r"(?<![A-Za-z0-9_./-])/" + re.escape(alias) + r"(?![A-Za-z0-9_-])",
            "/" + target,
            text,
        )

    # Replace namespaced references without renaming the actual agent or skill IDs.
    for source_id, target_id in sorted(skill_map.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(source_id, target_id)
    for name in sorted(agent_names, key=len, reverse=True):
        text = text.replace(f"maister:{name}", "maister-" + name)
    # Prefix every known agent ID in text, including prose/code references and subagent_type values.
    # Replace longest names first and only at identifier boundaries to avoid partial matches.
    for name in sorted(agent_names, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", "maister-" + name, text)
    text = re.sub(r"/maister:\*", "Maister slash command", text)
    text = re.sub(r"maister:\*", "Maister skill/command namespace", text)

    # Map Claude Code workflow tools to actual OpenCode tools or explicit manual fallbacks.
    replacements = [
        (r"\bAskUserQuestion\b", "question"),
        (r"\bTaskCreate\b", "todowrite"),
        (r"\bTaskUpdate\b", "todowrite"),
        (r"\bTaskList\b", "workflow state file and markdown plan checkboxes"),
        (r"\bTaskGet\b", "workflow state file and markdown plan checkboxes"),
        (r"\bEnterPlanMode\b", "OpenCode plan agent/mode if available"),
        (r"\bExitPlanMode\b", "OpenCode plan_exit"),
    ]
    for pattern, replacement in replacements: text = re.sub(pattern, replacement, text)
    # IMPORTANT: Do not collapse multi-line workflow sections. Earlier versions used
    # broad multi-line substitutions here and accidentally deleted substantial parts
    # of skills such as implementation-verifier. Preserve every task item and explain
    # native OpenCode todo limitations in-place instead.
    text = re.sub(
        r"(?m)^([ \t]*- `?)subject(`?:)",
        r"\1content (native OpenCode todo field; preserve original subject as workflow metadata)\2",
        text,
    )
    text = re.sub(
        r"(?m)^([ \t]*- `?)description(`?:)",
        r"\1acceptance criteria (store as workflow metadata; not a native todo field)\2",
        text,
    )
    text = re.sub(
        r"(?m)^([ \t]*- `?)activeForm(`?:)",
        r"\1activeForm (workflow metadata; not a native todo field)\2",
        text,
    )
    text = re.sub(
        r"(?m)^([ \t]*- )Subject:",
        r"\1Todo content (keep subject text; native field is content):",
        text,
    )
    text = re.sub(
        r"(?m)^([ \t]*- )activeForm:",
        r"\1Workflow active-form metadata (not a native todo field):",
        text,
    )
    # Native OpenCode todo items support content/status/priority only. Preserve other requested metadata in state files.
    text = re.sub(r"metadata:\s*\{([^}]+)\}", r"record metadata {\1} in orchestrator-state.yml (not a native todo field)", text)
    text = re.sub(r"owner:\s*([\"'][^\"']+[\"'])", r"record owner \1 in orchestrator-state.yml (not a native todo field)", text)
    # OpenCode todowrite replaces the complete list; create all items in one full-list call.
    text = re.sub(r"For each task group, call `?todowrite`?", "Create one todo item per task group in a single full-list `todowrite` call", text, flags=re.I)
    text = re.sub(r"One `?todowrite`? per task group", "One todo item per task group, created in the full-list `todowrite` call", text, flags=re.I)
    text = re.sub(r"use `?todowrite`? for all phases", "create all phase items in one full-list `todowrite` call", text, flags=re.I)
    text = re.sub(r"create them with `?todowrite`? for each task group", "create all task-group items in one full-list `todowrite` call", text, flags=re.I)

    # Clean up sentences whose grammar changed when TaskList was mapped to durable files.
    text = re.sub(r"Call `?workflow state file and markdown plan checkboxes`? to find existing task group items from the planner\. If found, use them\. If not, create them with `?todowrite`? for each task group \(when the planner created none\)\.", "Check orchestrator-state.yml and implementation-plan.md for existing task-group items. If none exist, create all task-group todos in one full-list `todowrite` call.", text)
    text = re.sub(r"All group tasks are `completed` via `workflow state file and markdown plan checkboxes` \(cross-validate against markdown checkboxes\)", "Verify all group tasks are completed by checking orchestrator-state.yml and markdown checkboxes", text)
    text = re.sub(r"Set dependencies with `record dependency edges[^`]*` mirroring the plan's dependency chain:", "Record dependency edges in orchestrator-state.yml, mirroring the plan's dependency chain; refresh the complete todo list using `todowrite` after status changes:", text)
    text = re.sub(r"One `OpenCode todowrite tool` per task group", "One todo item per task group, created in a single full-list `todowrite` call", text)
    text = text.replace("subagent_type: general-purpose", "subagent_type: maister-information-gatherer")

    # Replace references to Claude Code's named built-in tools only in tool contexts, not ordinary English.
    for old, new in [("Read", "read"), ("Write", "write"), ("Edit", "edit"), ("Bash", "bash"), ("Glob", "glob"), ("Grep", "grep"), ("WebSearch", "websearch"), ("WebFetch", "webfetch"), ("Skill", "skill"), ("Task", "task")]:
        text = re.sub(r"\b" + old + r"\s+tool\b", f"OpenCode {new} tool", text, flags=re.I)
    text = re.sub(r"\bUse Glob\b", "Use OpenCode glob tool", text)
    text = re.sub(r"\bUse Grep\b", "Use OpenCode grep tool", text)
    text = re.sub(r"\bUse Edit tool\b", "Use OpenCode edit tool", text)
    text = re.sub(r"\bUse (?:Read|Write|Bash) tool\b", lambda m: "Use OpenCode " + m.group(0).split()[1].lower() + " tool", text)
    text = re.sub(r"\bTask\(([A-Za-z0-9_-]+)\)", r"OpenCode task delegation for \1", text)

    # Avoid invoking Claude's built-in Explore agent. The conversion has named Maister agents instead.
    text = text.replace('Use the Task tool with `subagent_type="Explore"` — one call per selected role, all in ONE message.', 'Use the OpenCode task tool with a valid subagent_type chosen from the installed agent IDs for each role; use project-analyzer for broad codebase exploration when appropriate — one call per selected role, all in ONE message.')
    text = text.replace('subagent_type="Explore"', 'subagent_type="project-analyzer"')
    text = re.sub(r"\bExplore agents?\b", "OpenCode codebase-analysis subagents selected from the installed Maister agent list", text)
    text = re.sub(r"\bbuilt-in Explore subagents\b", "OpenCode subagents selected from the installed Maister agent list", text, flags=re.I)
    text = text.replace("Every Explore agent prompt", "Every codebase-analysis subagent prompt")
    text = text.replace("each Explore agent", "each delegated analysis subagent")
    text = text.replace('Use the OpenCode task tool with `subagent_type="project-analyzer"` — one call per selected role, all in ONE message.', 'Use the OpenCode task tool with a valid `subagent_type` selected from the installed agent IDs for each role; use `maister-project-analyzer` for broad codebase exploration when appropriate — one call per selected role, all in ONE message.')
    # Final reference pass catches IDs introduced by earlier semantic rewrites.
    for name in sorted(agent_names, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", "maister-" + name, text)
    return text

def validate(bundle: Path, expected_agents: int, expected_skills: int, expected_commands: int) -> list[str]:
    errors = []
    agents = sorted((bundle / "agents").glob("*.md"))
    skills = sorted((bundle / "skills").glob("*/SKILL.md"))
    commands = sorted((bundle / "commands").rglob("*.md"))
    if len(agents) != expected_agents: errors.append(f"Agent count mismatch: expected {expected_agents}, got {len(agents)}")
    if len(skills) != expected_skills: errors.append(f"Skill count mismatch: expected {expected_skills}, got {len(skills)}")
    if len(commands) != expected_commands: errors.append(f"Command count mismatch: expected {expected_commands}, got {len(commands)}")
    for p in agents:
        s = p.read_text(encoding="utf-8"); fm, _ = parse_frontmatter(s)
        if not p.stem.startswith("maister-"): errors.append(f"Agent ID is missing maister- prefix: {p}")
        if not FM_RE.match(s): errors.append(f"Agent lacks frontmatter: {p}")
        if not fm.get("description"): errors.append(f"Agent lacks description: {p}")
        if fm.get("mode") != "subagent": errors.append(f"Agent mode is not subagent: {p}")
        if "color" in fm: errors.append(f"Agent color must be omitted so OpenCode uses its default: {p}")
        header = FM_RE.match(s).group(1) if FM_RE.match(s) else ""
        if re.search(r"^model:\s*inherit\s*$", header, re.M): errors.append(f"Source model: inherit should be omitted so OpenCode inherits active model: {p}")
    for p in skills:
        s = p.read_text(encoding="utf-8"); fm, _ = parse_frontmatter(s)
        if not p.parent.name.startswith("maister-"): errors.append(f"Skill ID is missing maister- prefix: {p}")
        if not FM_RE.match(s): errors.append(f"Skill lacks frontmatter: {p}")
        if fm.get("name") != p.parent.name: errors.append(f"Skill name must match directory: {p}")
        if not SKILL_RE.fullmatch(fm.get("name", "")): errors.append(f"Invalid skill name: {p}")
        if not 1 <= len(fm.get("description", "")) <= 1024: errors.append(f"Missing/invalid skill description: {p}")
    for p in commands:
        s = p.read_text(encoding="utf-8"); fm, _ = parse_frontmatter(s)
        if not p.stem.startswith("maister-"): errors.append(f"Command ID is missing maister- prefix: {p}")
        if not FM_RE.match(s): errors.append(f"Command lacks frontmatter: {p}")
        if not fm.get("description"): errors.append(f"Command lacks description: {p}")
    try: json.loads((bundle / "opencode.json").read_text(encoding="utf-8"))
    except Exception as e: errors.append(f"Invalid opencode.json: {e}")
    plugin = bundle / "plugins/maister-compatibility.ts"
    if not plugin.is_file(): errors.append("Missing Maister compatibility plugin")
    # Known Claude-specific tool identifiers must not remain in active instructions (excluding report and installer).
    active = [*agents, *commands, *[p for p in (bundle / "skills").rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXTS], bundle / "AGENTS.md"]
    for p in active:
        txt = p.read_text(encoding="utf-8", errors="replace")
        for token in CLAUDE_TOOL_TOKENS:
            if re.search(r"\b" + token + r"\b", txt): errors.append(f"Unmapped Claude-only tool reference {token}: {p}")
        if "/maister:" in txt: errors.append(f"Unconverted Maister slash-command reference: {p}")
    known_agents = {p.stem for p in agents}
    for p in active:
        txt = p.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"subagent_type\s*[:=]\s*[\"'`]?([a-z][a-z0-9_-]+)", txt):
            agent_id = match.group(1)
            if agent_id in {"valid", "choose", "installed", "from", "the"}: continue
            if agent_id not in known_agents:
                errors.append(f"Unknown subagent_type {agent_id!r}: {p}")
    return errors

INSTALL_PS1 = r'''param([switch]$Force)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = Join-Path $HOME '.config\opencode'
New-Item -ItemType Directory -Force -Path $dst, (Join-Path $dst 'agents'), (Join-Path $dst 'skills'), (Join-Path $dst 'commands'), (Join-Path $dst 'plugins') | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $HOME ".opencode-maister-backup-$stamp"
$changed = $false

function Backup-Path([string]$path) {
  if (Test-Path $path) {
    $rel = [IO.Path]::GetRelativePath($dst, $path)
    $to = Join-Path $backup $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $to -Parent) | Out-Null
    Copy-Item $path $to -Recurse -Force
    $script:changed = $true
  }
}

# Copy individual agents; never overwrite existing files unless -Force is supplied.
Get-ChildItem (Join-Path $src 'agents') -Filter '*.md' -File | ForEach-Object {
  $to = Join-Path (Join-Path $dst 'agents') $_.Name
  if ((Test-Path $to) -and -not $Force) { Write-Warning "Skipping existing agent: $($_.Name) (use -Force to replace)"; return }
  Backup-Path $to
  Copy-Item $_.FullName $to -Force
}

# Copy skill directories. If a skill already exists, skip unless -Force; back up before replacing.
Get-ChildItem (Join-Path $src 'skills') -Directory | ForEach-Object {
  $to = Join-Path (Join-Path $dst 'skills') $_.Name
  if ((Test-Path $to) -and -not $Force) { Write-Warning "Skipping existing skill: $($_.Name) (use -Force to replace)"; return }
  Backup-Path $to
  if (Test-Path $to) { Remove-Item $to -Recurse -Force }
  Copy-Item $_.FullName $to -Recurse -Force
}

# Copy native OpenCode slash commands. Preserve relative paths and back up collisions.
$commandsSource = Join-Path $src 'commands'
if (Test-Path $commandsSource) {
  Get-ChildItem $commandsSource -Filter '*.md' -File -Recurse | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($commandsSource, $_.FullName)
    $to = Join-Path (Join-Path $dst 'commands') $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $to -Parent) | Out-Null
    if ((Test-Path $to) -and -not $Force) { Write-Warning "Skipping existing command: $relative (use -Force to replace)"; return }
    Backup-Path $to
    Copy-Item $_.FullName $to -Force
  }
}

$pluginFrom = Join-Path $src 'plugins\maister-compatibility.ts'
$pluginTo = Join-Path $dst 'plugins\maister-compatibility.ts'
if ((Test-Path $pluginTo) -and -not $Force) { Write-Warning 'Skipping existing maister-compatibility.ts (use -Force to replace)' }
else { Backup-Path $pluginTo; Copy-Item $pluginFrom $pluginTo -Force }

# Merge global AGENTS.md with a marked section; preserve user instructions.
$agentsFrom = Join-Path $src 'AGENTS.md'
$agentsTo = Join-Path $dst 'AGENTS.md'
$markerStart = '<!-- BEGIN MAISTER OPENCODE COMPATIBILITY -->'
$markerEnd = '<!-- END MAISTER OPENCODE COMPATIBILITY -->'
$incoming = Get-Content $agentsFrom -Raw
$existing = if (Test-Path $agentsTo) { Get-Content $agentsTo -Raw } else { '' }
if ($existing.Contains($markerStart) -and -not $Force) {
  Write-Warning 'Global AGENTS.md already contains a Maister section; leaving it unchanged (use -Force to replace that section).'
} else {
  Backup-Path $agentsTo
  if ($existing.Contains($markerStart)) {
    $existing = [regex]::Replace($existing, '(?s)<!-- BEGIN MAISTER OPENCODE COMPATIBILITY -->.*?<!-- END MAISTER OPENCODE COMPATIBILITY -->', '').Trim()
  }
  $merged = ($existing.Trim() + "`r`n`r`n" + $markerStart + "`r`n" + $incoming.Trim() + "`r`n" + $markerEnd + "`r`n").TrimStart()
  Set-Content -Path $agentsTo -Value $merged -Encoding utf8
}

# Merge MCP servers without replacing unrelated global settings.
$configFrom = Join-Path $src 'opencode.json'
$configTo = Join-Path $dst 'opencode.json'
$incomingConfig = Get-Content $configFrom -Raw | ConvertFrom-Json -AsHashtable
$current = if (Test-Path $configTo) { Get-Content $configTo -Raw | ConvertFrom-Json -AsHashtable } else { @{'$schema'='https://opencode.ai/config.json'} }
if (-not $current.ContainsKey('$schema')) { $current['$schema'] = 'https://opencode.ai/config.json' }
if ($incomingConfig.ContainsKey('mcp')) {
  if (-not $current.ContainsKey('mcp')) { $current['mcp'] = @{} }
  foreach ($name in $incomingConfig.mcp.Keys) {
    if ($current.mcp.ContainsKey($name) -and ($current.mcp[$name] | ConvertTo-Json -Depth 50 -Compress) -ne ($incomingConfig.mcp[$name] | ConvertTo-Json -Depth 50 -Compress) -and -not $Force) {
      throw "MCP server '$name' already exists with different settings. Review opencode.json or rerun with -Force after backing up."
    }
    $current.mcp[$name] = $incomingConfig.mcp[$name]
  }
}
Backup-Path $configTo
$current | ConvertTo-Json -Depth 50 | Set-Content -Path $configTo -Encoding utf8

if ($changed) { Write-Host "Backup created at: $backup" }
Write-Host "Maister files copied to $dst"
Write-Host 'Restart OpenCode, then run the smoke tests in VALIDATION-REPORT.md.'
'''

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "source", nargs="?", type=Path,
        help="Optional Maister repository/plugin directory or ZIP. By default, searches the repository containing this script."
    )
    ap.add_argument(
        "--target", type=Path,
        default=Path(__file__).resolve().parent / "maister-opencode",
        help="Output bundle directory (default: ./maister-opencode next to this script)"
    )
    ap.add_argument("--force", action="store_true", help="Allow overwriting generated files in output target")
    args = ap.parse_args()
    target = args.target.resolve()
    try:
        reader, source_description = open_source(args.source)
        with reader as z:
            bad = z.testzip()
            if bad: raise ValueError(f"ZIP integrity error: {bad}")
            names = [n for n in z.namelist() if safe_name(n) and not n.endswith("/")]
            root = find_root(z)
            rooted = [n for n in names if n.startswith(root)]
            agents_src = sorted(n for n in rooted if n.startswith(root + "agents/") and n.endswith(".md"))
            skills_src = sorted(n for n in rooted if n.startswith(root + "skills/") and n.endswith("/SKILL.md"))
            command_files = sorted(n for n in rooted if n.startswith(root + "commands/") and n.endswith(".md"))
            if not agents_src or not skills_src: raise ValueError("No agent or skill definitions found")
            command_map: dict[str, str] = {}
            command_names: dict[str, str] = {}
            for command_path in command_files:
                command_fm, _ = parse_frontmatter(z.read(command_path).decode("utf-8", errors="strict"))
                file_stem = PurePosixPath(command_path).stem.lower()
                declared_name = command_fm.get("name", "").strip().lower()
                source_name = declared_name.rsplit(":", 1)[-1] if declared_name else file_stem
                command_slug = source_name if source_name.startswith("maister-") else "maister-" + source_name
                if not AGENT_RE.fullmatch(command_slug):
                    raise ValueError(f"Invalid command name {declared_name or file_stem!r} in {command_path}")
                if command_slug in command_names.values():
                    raise ValueError(f"Duplicate converted command name: {command_slug}")
                command_names[command_path] = command_slug
                for alias in {file_stem, source_name, declared_name}:
                    if alias:
                        previous = command_map.get(alias)
                        if previous and previous != command_slug:
                            raise ValueError(f"Ambiguous command alias {alias!r}: {previous} vs {command_slug}")
                        command_map[alias] = command_slug
            if target.exists() and any(target.iterdir()) and not args.force:
                raise FileExistsError(f"Output target is not empty: {target}; use a clean target or --force")
            warnings: list[str] = []
            outputs: dict[Path, bytes] = {}
            # Converted mirror keyed by the ORIGINAL source-relative path. This preserves
            # original agent filenames and skill directory names so users can diff the
            # converted files against an unmodified extraction of the source ZIP.
            source_mirror: dict[Path, bytes] = {}
            source_root_path = Path(*PurePosixPath(root.rstrip("/")).parts)
            skill_prefix = root + "skills/"
            skill_map: dict[str, str] = {}
            skill_dirs: dict[str, str] = {}
            agent_names: set[str] = {PurePosixPath(n).stem.lower() for n in agents_src}
            for n in skills_src:
                src = z.read(n).decode("utf-8", errors="strict")
                fm, _ = parse_frontmatter(src)
                directory = PurePosixPath(n).parent.name
                new_name = skill_slug(fm.get("name", ""), directory)
                if new_name in skill_dirs.values() and skill_dirs.get(directory) != new_name:
                    raise ValueError(f"Duplicate normalized skill name: {new_name}")
                skill_dirs[directory] = new_name
                source_name = fm.get("name", "")
                if source_name: skill_map[source_name] = new_name
                skill_map["maister:" + directory] = new_name
                skill_map["maister:" + new_name] = new_name
            # Preserve all skill files and references/resources. Rewrite all text resources only.
            skill_line_audit: list[tuple[str, int, int]] = []
            for n in rooted:
                if not n.startswith(skill_prefix): continue
                rel = PurePosixPath(n[len(skill_prefix):])
                if not rel.parts: continue
                old_dir = rel.parts[0]
                new_dir = skill_dirs.get(old_dir, old_dir)
                out = target / "skills" / Path(new_dir, *rel.parts[1:])
                raw = z.read(n)
                if rel.name == "SKILL.md":
                    src = raw.decode("utf-8", errors="strict"); fm, body = parse_frontmatter(src)
                    slug = skill_dirs[old_dir]
                    desc = fm.get("description", "").strip()
                    desc = adapt_skill_references(adapt_text(desc, skill_map, agent_names, warnings, n + " (description)", command_map), skill_dirs)
                    if not desc:
                        heading = re.search(r"^#\s+(.+)$", body, re.M)
                        desc = f"Maister {heading.group(1).strip()} workflow." if heading else f"Maister workflow for {slug.replace('-', ' ')}."
                    if len(desc) > 1024:
                        warnings.append(f"{n}: skill description truncated to 1024 characters")
                        desc = desc[:1024]
                    original_body = body
                    body = adapt_skill_references(adapt_text(body, skill_map, agent_names, warnings, n, command_map), skill_dirs)
                    original_lines = len(original_body.splitlines())
                    converted_lines = len(body.splitlines())
                    skill_line_audit.append((n, original_lines, converted_lines))
                    if original_lines >= 40 and converted_lines < int(original_lines * 0.75):
                        raise ValueError(
                            f"Refusing lossy conversion of {n}: body shrank from {original_lines} to {converted_lines} lines. "
                            "Review transformation rules instead of dropping workflow content."
                        )
                    # OpenCode SKILL.md frontmatter: required name + description; omit Claude-only keys.
                    raw = (f"---\nname: {slug}\ndescription: {yaml_quote(desc)}\n---\n\n" + body.lstrip()).encode("utf-8")
                elif PurePosixPath(n).suffix.lower() in TEXT_EXTS:
                    raw = adapt_skill_references(adapt_text(raw.decode("utf-8", errors="replace"), skill_map, agent_names, warnings, n, command_map), skill_dirs).encode("utf-8")
                outputs[out] = raw
                original_rel = PurePosixPath(n[len(root):])
                mirror_path = target / "converted-source" / source_root_path / Path(*original_rel.parts)
                source_mirror[mirror_path] = raw
            # Agents: OpenCode uses filename as agent ID; do not pin inherited Claude model.
            for n in agents_src:
                src = z.read(n).decode("utf-8", errors="strict"); fm, body = parse_frontmatter(src)
                source_slug = PurePosixPath(n).stem.lower()
                if not AGENT_RE.fullmatch(source_slug): raise ValueError(f"Invalid agent filename: {n}")
                slug = source_slug if source_slug.startswith("maister-") else "maister-" + source_slug
                desc = fm.get("description", "").strip() or f"Maister {source_slug.replace('-', ' ')} specialist agent."
                desc = adapt_skill_references(adapt_text(desc, skill_map, agent_names, warnings, n + " (description)", command_map), skill_dirs)
                body = adapt_skill_references(adapt_text(body, skill_map, agent_names, warnings, n, command_map), skill_dirs)
                header = f"---\ndescription: {yaml_quote(desc)}\nmode: subagent\n"
                converted_agent = (header + "---\n\n" + body.lstrip()).encode("utf-8")
                outputs[target / "agents" / f"{slug}.md"] = converted_agent
                original_rel = PurePosixPath(n[len(root):])
                mirror_path = target / "converted-source" / source_root_path / Path(*original_rel.parts)
                source_mirror[mirror_path] = converted_agent

            # Commands: convert Claude slash commands to native OpenCode Markdown commands.
            # The filename defines the OpenCode command ID; Claude's `name` key is not copied.
            for n in command_files:
                src = z.read(n).decode("utf-8", errors="strict")
                fm, body = parse_frontmatter(src)
                slug = command_names[n]
                desc = fm.get("description", "").strip() or f"Maister {slug.removeprefix('maister-').replace('-', ' ')} command."
                desc = adapt_skill_references(
                    adapt_text(desc, skill_map, agent_names, warnings, n + " (description)", command_map),
                    skill_dirs,
                )
                body = adapt_skill_references(
                    adapt_text(body, skill_map, agent_names, warnings, n, command_map),
                    skill_dirs,
                )
                # Claude's <command-name> is not an OpenCode template variable.
                body = body.replace("<command-name>", "/" + slug)
                command_rel = PurePosixPath(n[len(root + "commands/"):])
                command_output_rel = Path(*command_rel.parts[:-1], slug + ".md")
                converted_command = (
                    f"---\ndescription: {yaml_quote(desc)}\n---\n\n" + body.lstrip()
                ).encode("utf-8")
                command_output = target / "commands" / command_output_rel
                outputs[command_output] = converted_command
                mirror_path = target / "converted-source" / source_root_path / Path(*PurePosixPath(n[len(root):]).parts)
                source_mirror[mirror_path] = converted_command

            mcp_path = root + ".mcp.json"
            mcp_cfg = convert_mcp(z.read(mcp_path)) if mcp_path in rooted else {}
            if mcp_path not in rooted: warnings.append("No .mcp.json found; no MCP servers configured by this conversion.")
            cfg = {"$schema": "https://opencode.ai/config.json"}
            if mcp_cfg: cfg["mcp"] = mcp_cfg
            outputs[target / "opencode.json"] = (json.dumps(cfg, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            outputs[target / "plugins/maister-compatibility.ts"] = PLUGIN_TS.encode("utf-8")

            guidance = """# Global Maister compatibility instructions for OpenCode

This section adapts the Maister workflow to OpenCode. Keep the original workflow gates and verification standards wherever possible.

## Tool mapping and behavior
- Questions: use OpenCode's `question` tool with explicit choices when available. If unavailable in this client/session, ask the user in chat and WAIT for the answer. Never continue past mandatory approval gates without an answer.
- Skill loading: use the OpenCode `skill` tool with the exact skill ID from the available skill list. Skill IDs are the directory names under the global `skills/` directory (converted Maister IDs start with `maister-`); do not use the source plugin's colon-prefixed skill namespace.
- Delegation: use OpenCode's `task` tool with an exact installed agent ID from the global `agents/` directory (converted Maister agent IDs start with `maister-`). Do not request Claude Code built-in agents such as `Explore`; choose the closest available Maister agent by its description. If no suitable agent exists, state the limitation.
- Task tracking: OpenCode 1.19.0 provides `todowrite`, which replaces the entire list. Every todo requires `content`, `status`, and `priority` (`low`, `medium`, or `high`). There is no native `todoread` tool in this release; read durable state files and markdown checkboxes to inspect prior progress. Store metadata, ownership, active-form labels, and dependency edges in orchestrator-state.yml. The primary agent owns the todo list; subagents report results to the parent.
- Planning: use OpenCode plan mode / `plan_exit` only if enabled and available. Otherwise write the plan, present it to the user, and wait for explicit approval before implementation.
- File and shell tools: use OpenCode's native `read`, `write`, `edit`, `bash`, `glob`, and `grep` tools when available. Do not claim that a tool was called, a test passed, or an agent ran unless it actually happened.
- Never silently skip a required workflow phase. If an operation depends on a Claude Code-only feature with no equivalent, explain the limitation and ask the user before substituting a material step.

## Commands and hooks
Maister slash commands are converted to native OpenCode Markdown commands under commands/ with maister- prefixed IDs (for example /maister-work). Invoke these from the OpenCode command menu. Destructive-command blocking is a best-effort plugin guard, not a security boundary. Keep native OpenCode permissions enabled.
"""
            outputs[target / "AGENTS.md"] = guidance.encode("utf-8")
            outputs[target / "Install-Global-Maister.ps1"] = INSTALL_PS1.encode("utf-8")
            outputs[target / Path(__file__).name] = Path(__file__).read_bytes()
            hook_files = [n for n in rooted if n.startswith(root + "hooks/") and not n.endswith("/")]
            # scan all active text outputs for unported Claude tool tokens, skill namespaces and invalid Explore subagent refs
            report = [
                "# Maister → OpenCode 1.19.0 conversion report", "",
                f"- Source: `{source_description}`", f"- Detected Claude plugin root: `{root}`",
                f"- Agents converted: {len(agents_src)}", f"- Skills converted: {len(skills_src)}",
                f"- Slash commands converted: {len(command_files)}", f"- Source hook files inspected but never executed/copied: {len(hook_files)}",
                f"- MCP servers mapped: {len(mcp_cfg)}", f"- Generated files: {len(outputs) + len(source_mirror) + 1}", f"- Diff mirror: `converted-source/{root.rstrip('/')}/` preserves original source paths and filenames; file contents are converted, not pristine originals.", "",
                "## Claude-to-OpenCode semantic mappings", "",
                "- Claude slash commands → native OpenCode Markdown commands in `commands/`, named with the `maister-` prefix.",
                "- `AskUserQuestion` → OpenCode `question` tool; global instructions specify chat fallback and mandatory wait for explicit answers.",
                "- `TaskCreate` / `TaskUpdate` → `todowrite`; send the complete list on every update. Native todo fields are `content`, `status`, and `priority` only; all three are required.",
                "- `TaskList` / `TaskGet` → read the durable workflow state file and markdown plan checkboxes; OpenCode 1.19.0 has no native `todoread` tool.",
                "- `TaskUpdate addBlockedBy` → persist dependency edges in `orchestrator-state.yml` and refresh the full todo list; native todos do not model dependency edges.",
                "- `EnterPlanMode` / `ExitPlanMode` → OpenCode plan mode / `plan_exit` when available, otherwise written plan and explicit user approval.",
                "- Claude `Read`/`Write`/`Edit`/`Bash`/`Glob`/`Grep`/`Skill`/`Task` tool references → OpenCode native tool names in tool-context phrases.",
                "- Source hook scripts are never executed. OpenCode plugin implements a pattern-based shell guard, system instructions, and compaction context.", "",
                "## Content-preservation audit", "",
                "The converter records original vs converted body line counts for each SKILL.md and fails if a skill body of at least 40 lines shrinks below 75% of its original line count. Multi-line workflow sections are not replaced wholesale. Line counts are a regression guard, not proof of semantic equivalence.",
                *[f"- `{name}`: {before} source body lines → {after} converted body lines" for name, before, after in skill_line_audit],
                "", "## Static validation", "",
                "The converter validates output counts, agent/skill/command frontmatter, prefixed IDs, skill slug matching, JSON syntax, plugin presence, namespaced agent/skill/command references, and absence of known Claude-only workflow tool identifiers in active converted instructions. The diff mirror preserves original source-relative paths and filenames under `converted-source/<original-plugin-root>/`, but contains converted content.",
                "Static validation does not launch OpenCode, type-check against the installed SDK, test the runtime hook, or prove that Qwen follows every workflow.", "",
                "## Warnings / unresolved references", "",
            ]
            report += ["- " + w for w in sorted(set(warnings))] if warnings else ["- No unresolved Maister slash-command references were found by the converter."]
            report += ["", "## Required runtime smoke tests", "",
                "1. Confirm `opencode --version` reports 1.19.0.",
                "2. Run `Install-Global-Maister.ps1` from PowerShell 7; review all skipped collisions and backup output.",
                "3. Restart OpenCode and inspect startup logs for plugin/config errors.",
                "4. Confirm `maister-*` agents are available, `maister-*` skills can be loaded with the `skill` tool, and `/maister-work` plus `/maister-reviews-code` appear in the command menu.",
                "5. In a disposable repository, test the shell guard with a known blocked command without executing destructive changes.",
                "6. Verify compaction preserves the active workflow state.",
                "7. Test `question`, todo updates, plan approval, and delegation with the exact Qwen model/client you use.",
                "8. Verify Playwright MCP separately if needed; its source command uses `npx` and may require Node/npm and network access.", ""]
            outputs[target / "VALIDATION-REPORT.md"] = ("\n".join(report)).encode("utf-8")
            outputs[target / "README-INSTALL.md"] = ("""# Install Maister globally in OpenCode 1.19.0

1. Extract this bundle to a temporary folder.
2. Open PowerShell 7 in that extracted folder.
3. Run `./Install-Global-Maister.ps1` (use `-Force` only after reviewing collisions; existing files are backed up before replacement).
4. Restart OpenCode. No project files need to be copied or modified.
5. Read `VALIDATION-REPORT.md` and run every runtime smoke test before trusting the workflow.

The bundle prefixes every converted agent, skill, and command ID with `maister-`. Claude slash commands become native OpenCode commands (for example `/maister-work`); known skill/agent references and slash-command references are rewritten in converted content. The `converted-source/<original-plugin-root>/` mirror preserves original source paths and filenames while storing converted content. The converter maps known Claude-specific workflow tools to OpenCode equivalents but cannot guarantee identical behavior under every model.
""").encode("utf-8")

            # Mirror files at their original source-relative paths and names, but with converted contents.
            # This lets users diff converted-source/<original-root>/ against a pristine extraction.
            outputs.update(source_mirror)

            # Add a report of source tool references transformed (counts aid auditability).
            # Avoid touching pre-existing target files unless --force.
            collisions = [str(p) for p in outputs if p.exists() and not args.force]
            if collisions: raise FileExistsError("Output target already contains generated files; use an empty target or --force. First collisions: " + ", ".join(collisions[:8]))
            for p, data in outputs.items():
                p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
            errors = validate(target, len(agents_src), len(skills_src), len(command_files))
            if errors:
                print("STATIC VALIDATION FAIL", file=sys.stderr)
                for e in errors: print(" - " + e, file=sys.stderr)
                return 2
            print(f"Static conversion and validation PASS ({VERSION}).")
            print(f"Agents: {len(agents_src)}; skills: {len(skills_src)}; commands: {len(command_files)}; MCP servers: {len(mcp_cfg)}")
            print(f"Global bundle: {target}")
            print(f"Report: {target / 'VALIDATION-REPORT.md'}")
            print("Runtime compatibility is not certified; run the smoke tests in VALIDATION-REPORT.md.")
            return 0
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
