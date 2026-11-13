"""Find out what a shader does with each property by reading what it writes.

Name lists do not scale. A project has hundreds of custom shaders, and every one
of them is free to call its metallic map `_Specular`, tint with `_MulColor`, or
put the normal in a slot called `_Normal` that the name heuristics then hand to
something else. Each of those is a separate patch, and the next project brings
new ones.

A Unity surface shader, however, has to state its intent: it assigns to a fixed,
closed set of outputs -- `o.Albedo`, `o.Normal`, `o.Metallic`, `o.Smoothness`,
`o.Occlusion`, `o.Emission`, `o.Alpha`, `o.Specular`, `o.Gloss` -- and those map
one to one onto a Principled BSDF. Tracing which property reaches which output
turns the mapping from a guess into a reading.

This is deliberately not an HLSL compiler. It follows straight-line assignments,
which is what Amplify emits and what hand written surface shaders mostly are,
and it says `blended` instead of inventing an answer when the expression does
something it cannot model. What it cannot read, it does not claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


ALBEDO = "Albedo"
NORMAL = "Normal"
METALLIC = "Metallic"
SMOOTHNESS = "Smoothness"
OCCLUSION = "Occlusion"
EMISSION = "Emission"
ALPHA = "Alpha"
SPECULAR = "Specular"
GLOSS = "Gloss"

OUTPUTS = (
    ALBEDO, NORMAL, METALLIC, SMOOTHNESS, OCCLUSION, EMISSION, ALPHA, SPECULAR, GLOSS,
)

# How far an output expression is expanded through local variables. Amplify
# chains run to a dozen or so; the cap is what keeps a cyclic or pathological
# body from turning into an unbounded string.
_MAX_DEPTH = 16
_MAX_LENGTH = 20000

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SURFACE_PRAGMA = re.compile(r"#pragma\s+surface\s+(?P<function>\w+)\s+(?P<model>\w+)")
_OUTPUT_WRITE = re.compile(
    r"\b(?P<target>\w+)\s*\.\s*(?P<output>" + "|".join(OUTPUTS) + r")\s*=(?P<expression>[^;]*);"
)
# Every way Unity and Amplify spell "sample this texture property".
_SAMPLE = re.compile(
    r"\b(?:tex2D|tex2Dlod|tex2Dbias|tex2Dgrad|tex2Dproj|tex3D|texCUBE|texCUBElod"
    r"|UNITY_SAMPLE_TEX2D(?:_SAMPLER)?|UNITY_SAMPLE_TEX2DARRAY"
    r"|SAMPLE_TEXTURE2D(?:_LOD|_GRAD)?|SAMPLE_TEXTURE2D_ARRAY)\s*\(\s*"
    r"(?P<property>[A-Za-z_][A-Za-z0-9_]*)"
)
_SWIZZLE = re.compile(r"\A\s*\.\s*(?P<swizzle>[rgbaxyzw]{1,4})\b")
# A statement that binds a local: an optional type, a name, then a single `=`.
_ASSIGNMENT = re.compile(
    r"\A\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*(?:<[^>]*>)?\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<expression>.+)\Z",
    re.DOTALL,
)
_BLEND_CALLS = ("lerp", "step", "smoothstep", "min", "max")

COLOUR_SWIZZLES = frozenset({"", "rgb", "rgba", "xyz", "xyzw"})


@dataclass(frozen=True)
class TextureUse:
    """One texture property sampled into an output, and the part that is used."""

    property_name: str
    swizzle: str = ""

    @property
    def is_full_colour(self) -> bool:
        """True when the whole sample is used, not one channel picked out of it.

        A single channel multiplied into the albedo is a mask -- occlusion, dirt,
        a blend weight -- while the full sample is the albedo itself. That is the
        difference between `_AO` and `_MainTex` without knowing either name.
        """
        return self.swizzle.casefold() in COLOUR_SWIZZLES

    def to_data(self) -> dict:
        return {"property": self.property_name, "swizzle": self.swizzle}

    @classmethod
    def from_data(cls, data: dict) -> "TextureUse":
        return cls(property_name=data.get("property", ""), swizzle=data.get("swizzle", ""))


@dataclass(frozen=True)
class OutputBinding:
    """What reaches one surface output."""

    output: str
    textures: tuple[TextureUse, ...] = field(default_factory=tuple)
    multiplied_colors: tuple[str, ...] = field(default_factory=tuple)
    colors: tuple[str, ...] = field(default_factory=tuple)
    scalars: tuple[str, ...] = field(default_factory=tuple)
    blended: bool = False

    def to_data(self) -> dict:
        return {
            "output": self.output,
            "textures": [item.to_data() for item in self.textures],
            "multiplied_colors": list(self.multiplied_colors),
            "colors": list(self.colors),
            "scalars": list(self.scalars),
            "blended": self.blended,
        }

    @classmethod
    def from_data(cls, data: dict) -> "OutputBinding":
        return cls(
            output=data.get("output", ""),
            textures=tuple(TextureUse.from_data(item) for item in data.get("textures", [])),
            multiplied_colors=tuple(data.get("multiplied_colors", ())),
            colors=tuple(data.get("colors", ())),
            scalars=tuple(data.get("scalars", ())),
            blended=bool(data.get("blended", False)),
        )


def _balanced_end(text: str, start: int, opening: str = "(", closing: str = ")") -> int:
    """Index just past the bracket group that opens at or after `start`."""
    index = text.find(opening, start)
    if index < 0:
        return -1
    depth = 0
    for position in range(index, len(text)):
        character = text[position]
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return position + 1
    return -1


def function_body(text: str, name: str) -> str:
    """The body of `name`, or an empty string when it cannot be isolated.

    Restricting the read to the surface function matters: a shader may also
    write `o.rgb` in a final-colour pass, and treating that as a surface output
    would attribute a fresnel tint to the albedo.
    """
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", text):
        arguments_end = _balanced_end(text, match.end() - 1)
        if arguments_end < 0:
            continue
        brace = text.find("{", arguments_end)
        if brace < 0 or text[arguments_end:brace].strip() not in ("", "\n"):
            continue
        end = _balanced_end(text, brace, "{", "}")
        if end > 0:
            return text[brace + 1:end - 1]
    return ""


def _statements(body: str) -> list[str]:
    """Split a function body into statements on top-level semicolons."""
    statements: list[str] = []
    depth = 0
    current: list[str] = []
    for character in body:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        if character == ";" and depth <= 0:
            statements.append("".join(current))
            current = []
            continue
        current.append(character)
    tail = "".join(current)
    if tail.strip():
        statements.append(tail)
    return statements


def _local_assignments(statements: list[str]) -> dict[str, str]:
    """Local variable -> the expression it was bound to, first binding wins.

    First rather than last because `#if`/`#else` branches bind the same name
    twice, and picking one arbitrarily at least picks the same one every run.
    """
    locals_: dict[str, str] = {}
    for statement in statements:
        cleaned = statement.strip()
        if not cleaned or cleaned.startswith("#") or "=" not in cleaned:
            continue
        head = cleaned.split("=", 1)[0]
        if "." in head or "==" in cleaned.split("=", 1)[1][:1]:
            continue
        match = _ASSIGNMENT.match(cleaned)
        if match is None:
            continue
        name = match.group("name")
        if name in locals_ or name in OUTPUTS:
            continue
        locals_[name] = match.group("expression")
    return locals_


def _expand(expression: str, locals_: dict[str, str]) -> str:
    """Substitute local variables until only properties and literals remain."""
    current = expression
    for _ in range(_MAX_DEPTH):
        replaced = False
        pieces: list[str] = []
        position = 0
        for match in _IDENTIFIER.finditer(current):
            body = locals_.get(match.group(0))
            if body is None:
                continue
            pieces.append(current[position:match.start()])
            pieces.append(f"({body})")
            position = match.end()
            replaced = True
        pieces.append(current[position:])
        current = "".join(pieces)
        if not replaced or len(current) > _MAX_LENGTH:
            break
    return current


def _sampled_textures(expression: str, textures: set[str]) -> tuple[TextureUse, ...]:
    found: dict[str, TextureUse] = {}
    for match in _SAMPLE.finditer(expression):
        name = match.group("property")
        if name not in textures:
            continue
        end = _balanced_end(expression, match.start())
        swizzle = ""
        if end > 0:
            tail = _SWIZZLE.match(expression[end:])
            if tail is not None:
                swizzle = tail.group("swizzle")
        # The widest use wins: a texture read once as `.r` and once whole is an
        # albedo that something also samples a channel of.
        previous = found.get(name)
        if previous is None or (not previous.is_full_colour and not swizzle):
            found[name] = TextureUse(property_name=name, swizzle=swizzle)
    return tuple(found.values())


def _enclosing_group(expression: str, position: int) -> str:
    depth = 0
    start = -1
    for index in range(position - 1, -1, -1):
        character = expression[index]
        if character == ")":
            depth += 1
        elif character == "(":
            if depth == 0:
                start = index
                break
            depth -= 1
    if start < 0:
        return expression
    end = _balanced_end(expression, start)
    return expression[start + 1:end - 1] if end > 0 else expression[start + 1:]


def _top_level_operators(group: str) -> set[str]:
    operators: set[str] = set()
    depth = 0
    for character in group:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and character in "+-*/?:":
            operators.add(character)
    return operators


def is_multiplied(expression: str, name: str) -> bool:
    """True when `name` sits in a product rather than in a sum or a blend.

    A shader that writes `( _Color0 + hsv ) * blend` offsets the albedo by a
    colour; multiplying by it would be a different shader. Only a name standing
    directly in a product counts as a factor.
    """
    for match in re.finditer(rf"\b{re.escape(name)}\b", expression):
        operators = _top_level_operators(_enclosing_group(expression, match.start()))
        if "*" in operators and not operators & set("+-/?:"):
            return True
    return False


def _is_blended(expression: str) -> bool:
    if any(f"{call}(" in expression.replace(" ", "") for call in _BLEND_CALLS):
        return True
    return "?" in expression


def analyze(
    text: str,
    *,
    textures: set[str],
    colors: set[str],
    scalars: set[str],
) -> tuple[OutputBinding, ...]:
    """Read the surface outputs of a ShaderLab body.

    `textures`, `colors` and `scalars` are the shader's own property names, so
    that a local variable that happens to share a name with nothing is ignored
    and only real properties are ever reported.
    """
    pragma = _SURFACE_PRAGMA.search(text)
    body = function_body(text, pragma.group("function")) if pragma else ""
    if not body:
        return ()

    statements = _statements(body)
    locals_ = _local_assignments(statements)

    bindings: dict[str, OutputBinding] = {}
    for match in _OUTPUT_WRITE.finditer(body):
        output = match.group("output")
        if output in bindings:
            continue
        expanded = _expand(match.group("expression"), locals_)
        names = dict.fromkeys(_IDENTIFIER.findall(expanded))
        bindings[output] = OutputBinding(
            output=output,
            textures=_sampled_textures(expanded, textures),
            multiplied_colors=tuple(
                name for name in names if name in colors and is_multiplied(expanded, name)
            ),
            colors=tuple(name for name in names if name in colors),
            scalars=tuple(name for name in names if name in scalars),
            blended=_is_blended(expanded),
        )
    return tuple(bindings[output] for output in OUTPUTS if output in bindings)
