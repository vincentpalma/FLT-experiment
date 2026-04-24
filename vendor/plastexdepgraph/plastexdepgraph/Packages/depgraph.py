"""
Package Dependency graphs

Options:
* title: the title of the dependency graph.
        The default value is Dependencies.
* dep_by: optional level for dependency graph generation, for instance chapter
        or part.
        The default value is to generate one graph for the whole document
* thms: optional list of theorem types to include into the report,
        separated by +.
        The default value is: definition+lemma+proposition+theorem+corollary
* nonreducedgraph: keep all edges in the dependency, even transitively
        redundant ones.
* tpl: template file for dependency graph, relative to the current
  directory

This package will also consider optional information contained in the document
userdata dictionary. Such information could be added by other packages who
want to influence the dependency graph.

* document.userdata['dep_graph']['shapes'] can be a dictionary whose keys are node
  kinds as strings and whose values are strings descripting graphviz shapes
  (see https://graphviz.org/doc/info/shapes.html).
  By default, everything uses an ellipse except definition which uses a box.

* document.userdata['dep_graph']['colorizer'] can be a function taking as input
  a plasTeX node and outputting a CSS color for the boundary of graph nodes.

* document.userdata['dep_graph']['fillcolorizer'] can be a function taking as
  input a plasTeX node and outputting a CSS color for the interior of graph
  nodes.

* document.userdata['dep_graph']['stylerizer'] can be a function taking as input
  a plasTeX node and outputting a graphviz style
  (see https://graphviz.org/docs/attr-types/style/).

* document.userdata['dep_graph']['legend'] can be a list whose entries are pairs
  made of a visual description and an explanation.
  The default value is:
  [('Boxes', 'definitions'), ('Ellipses', 'theorems and lemmas')]
  Additional entries can also refer to colors.

* document.userdata['dep_graph']['extra_modal_links'] can be a list of Jinja2
  templates used to render extra links at the bottom of the modal appearing
  when clicking on graph nodes.
  The default value is an empty list.
"""
import json
import string
from collections import defaultdict
from pathlib import Path
from typing import Optional

from jinja2 import Template
from pygraphviz import AGraph

from plasTeX import Command, Environment
from plasTeX.PackageResource import (
        PackageTemplateDir, PackageJs, PackageCss, PackagePreCleanupCB)

from plasTeX.Logging import getLogger
log = getLogger()

PKG_DIR = Path(__file__).parent
STATIC_DIR = Path(__file__).parent.parent/'static'
DEFAULT_TYPES = 'definition+lemma+proposition+theorem+corollary'
SECTION_ORDER = ('part', 'chapter', 'section', 'subsection', 'subsubsection')
NAVIGATION_SECTION_ORDER = ('chapter', 'section', 'subsection', 'subsubsection')


def item_kind(node) -> str:
    """Return the kind of declaration corresponding to node"""
    if hasattr(node, 'thmName'):
        return node.thmName
    if node.parentNode:
        return item_kind(node.parentNode)
    return ''


def text_content(node) -> str:
    """Best-effort plain-text extraction for plasTeX nodes."""
    if node is None:
        return ''
    return getattr(node, 'textContent', str(node))


def section_identifier(section) -> str:
    """Return a stable frontend identifier for a section-like node."""
    url = getattr(section, 'url', '')
    if url:
        return f'{section.nodeName}:{url}'
    node_id = getattr(section, 'id', '')
    if node_id:
        return f'{section.nodeName}:{node_id}'
    ref = text_content(getattr(section, 'ref', None)) or getattr(section, 'counter', '')
    title = text_content(getattr(section, 'title', None)) or text_content(getattr(section, 'tocEntry', None))
    return f'{section.nodeName}:{ref}:{title}'


def section_chain(node, root_section=None):
    """Return the containing section ancestry for a graph node, top-down."""
    chain = []
    seen = set()
    section = getattr(node, 'currentSection', None)
    root_section_id = section_identifier(root_section) if root_section is not None else None
    while section is not None and id(section) not in seen:
        seen.add(id(section))
        if getattr(section, 'nodeName', None) in SECTION_ORDER:
            chain.append(section)
        section = getattr(section, 'currentSection', None)
    chain.reverse()
    if root_section_id:
        for index, section in enumerate(chain):
            if section_identifier(section) == root_section_id:
                return chain[index:]
    return chain


def section_record(section) -> dict:
    """Serialize a plasTeX section node for frontend navigation."""
    title = text_content(getattr(section, 'title', None)) or text_content(getattr(section, 'tocEntry', None))
    ref = text_content(getattr(section, 'ref', None))
    counter = getattr(section, 'counter', '') or ''
    label_parts = []
    if counter:
        label_parts.append(counter.capitalize())
    elif getattr(section, 'nodeName', ''):
        label_parts.append(section.nodeName.capitalize())
    if ref:
        label_parts.append(ref)
    label = ' '.join(label_parts).strip()
    return {
        'id': section_identifier(section),
        'type': getattr(section, 'nodeName', ''),
        'level': getattr(section, 'level', None),
        'counter': counter,
        'ref': ref,
        'title': title,
        'label': label or title,
        'fullTitle': text_content(getattr(section, 'fullTitle', None)) or ' '.join(filter(None, [label, title])),
        'url': getattr(section, 'url', '') or '',
        'filename': getattr(section, 'filename', '') or '',
        'parentId': section_identifier(section.currentSection) if getattr(section, 'currentSection', None) is not None else None,
    }


def node_display_record(graph: 'DepGraph', node, shapes: dict) -> dict:
    """Serialize the Graphviz display attributes for a theorem node."""
    color = graph.document.userdata['dep_graph'].get('colorizer', lambda x: '')(node) or ''
    fillcolor = graph.document.userdata['dep_graph'].get('fillcolorizer', lambda x: '')(node) or ''
    if fillcolor:
        style = graph.document.userdata['dep_graph'].get('stylerizer', lambda x: 'filled')(node) or ''
    else:
        style = graph.document.userdata['dep_graph'].get('stylerizer', lambda x: '')(node) or ''

    return {
        'id': node.id,
        'label': node.id.split(':')[-1],
        'shape': shapes.get(item_kind(node), 'ellipse'),
        'style': style,
        'color': color,
        'fillcolor': fillcolor,
    }


def navigation_levels(section, section_records: dict) -> tuple[Optional[str], Optional[str]]:
    """Choose the primary and secondary navigation levels for a graph page."""
    present_types = {record['type'] for record in section_records.values()}
    if section is None:
        start_index = 0
        root_type = None
    else:
        root_type = getattr(section, 'nodeName', None)
        try:
            start_index = NAVIGATION_SECTION_ORDER.index(root_type) + 1
        except ValueError:
            start_index = 0
    primary = next((name for name in NAVIGATION_SECTION_ORDER[start_index:] if name in present_types), None)
    if primary is None and root_type in present_types:
        primary = root_type
    if primary is None:
        primary = next((name for name in NAVIGATION_SECTION_ORDER if name in present_types), None)
    if primary is None:
        return None, None
    primary_index = NAVIGATION_SECTION_ORDER.index(primary)
    secondary = next((name for name in NAVIGATION_SECTION_ORDER[primary_index + 1:] if name in present_types), None)
    return primary, secondary


def ordered_section_ids(section, level_name: str, allowed_ids: set[str]) -> list[str]:
    """Return section ids in document order for a given level within the current graph root."""
    ordered = []
    if getattr(section, 'nodeName', None) == level_name:
        root_id = section_identifier(section)
        if root_id in allowed_ids:
            ordered.append(root_id)
    for child in section.getElementsByTagName(level_name):
        child_id = section_identifier(child)
        if child_id in allowed_ids:
            ordered.append(child_id)
    return ordered

def edge_records_from_graph(graph: 'DepGraph') -> list[dict]:
    """Serialize the full semantic dependency graph before transitive reduction."""
    records = []
    for source, target in graph.edges:
        if source in graph.nodes and target in graph.nodes:
            records.append({
                'id': f'dependency:{source.id}->{target.id}',
                'source': source.id,
                'target': target.id,
                'kind': 'dependency',
            })
    for source, target in graph.proof_edges:
        if source in graph.nodes and target in graph.nodes:
            records.append({
                'id': f'proof:{source.id}->{target.id}',
                'source': source.id,
                'target': target.id,
                'kind': 'proof',
            })
    return sorted(records, key=lambda item: (item['source'], item['target'], item['kind']))


def display_edge_records_from_graph(graph: 'DepGraph', shapes: dict, reduce_graph: bool) -> list[dict]:
    """Serialize the currently displayed graph edges used by Graphviz."""
    dot = graph.to_dot(shapes)
    if reduce_graph:
        dot = dot.tred()

    records = []
    for edge in dot.edges():
        source = str(edge[0])
        target = str(edge[1])
        style = edge.attr.get('style', '') or ''
        kind = 'dependency' if 'dashed' in style else 'proof'
        records.append({
            'id': f'{kind}:{source}->{target}',
            'source': source,
            'target': target,
            'kind': kind,
        })
    return sorted(records, key=lambda item: (item['source'], item['target'], item['kind']))


def collapsed_view(node_ids: set[str], all_node_ids: set[str], edge_records: list[dict]) -> dict:
    """
    Build a collapsed view for a highlighted node subset.

    Hidden intermediate results are detected from the semantic dependency graph
    supplied by the caller.
    """
    visible = set(node_ids)
    hidden = all_node_ids - visible
    direct_edge_lookup = defaultdict(list)
    hidden_out = defaultdict(set)
    hidden_in = defaultdict(set)
    source_to_hidden = defaultdict(set)
    hidden_to_target = defaultdict(set)
    visible_edge_ids = []

    for edge in edge_records:
        source = edge['source']
        target = edge['target']
        if source in visible and target in visible:
            visible_edge_ids.append(edge['id'])
            direct_edge_lookup[(source, target)].append(edge['id'])
        elif source in hidden and target in hidden:
            hidden_out[source].add(target)
            hidden_in[target].add(source)
        elif source in visible and target in hidden:
            source_to_hidden[source].add(target)
        elif source in hidden and target in visible:
            hidden_to_target[target].add(source)

    forward_cache = {}
    backward_cache = {}

    def forward_hidden(seed: str) -> set[str]:
        cached = forward_cache.get(seed)
        if cached is not None:
            return cached
        seen = set()
        stack = [seed]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(hidden_out.get(current, ()))
        forward_cache[seed] = seen
        return seen

    def backward_hidden(seed: str) -> set[str]:
        cached = backward_cache.get(seed)
        if cached is not None:
            return cached
        seen = set()
        stack = [seed]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(hidden_in.get(current, ()))
        backward_cache[seed] = seen
        return seen

    reachable_from_visible = {}
    for source in visible:
        hidden_nodes = set()
        for seed in source_to_hidden.get(source, ()):
            hidden_nodes.update(forward_hidden(seed))
        reachable_from_visible[source] = hidden_nodes

    reaching_visible = {}
    for target in visible:
        hidden_nodes = set()
        for seed in hidden_to_target.get(target, ()):
            hidden_nodes.update(backward_hidden(seed))
        reaching_visible[target] = hidden_nodes

    collapsed_edges = []
    for source in sorted(visible):
        for target in sorted(visible):
            if source == target:
                continue
            hidden_nodes = sorted(reachable_from_visible[source].intersection(reaching_visible[target]))
            if not hidden_nodes:
                continue
            collapsed_edges.append({
                'id': f'collapsed:{source}->{target}',
                'source': source,
                'target': target,
                'count': len(hidden_nodes),
                'hiddenNodeIds': hidden_nodes,
                'directEdgeIds': direct_edge_lookup.get((source, target), []),
            })

    return {
        'nodeIds': sorted(visible),
        'edgeIds': sorted(visible_edge_ids),
        'collapsedEdges': collapsed_edges,
    }


def navigation_payload(section, graph, semantic_edge_records: list[dict], display_edge_records: list[dict], shapes: dict) -> dict:
    """Build the chapter/subchapter payload consumed by navigation UI code."""
    section_records = {}
    section_node_ids = defaultdict(set)
    node_records = []

    for node in sorted(graph.nodes, key=lambda item: item.id):
        chain = section_chain(node, None if section == graph.document else section)
        section_ids = []
        for section_node in chain:
            section_id = section_identifier(section_node)
            if section_id not in section_records:
                section_records[section_id] = section_record(section_node)
            section_ids.append(section_id)
            section_node_ids[section_id].add(node.id)
        node_record = node_display_record(graph, node, shapes)
        node_record['sectionIds'] = section_ids
        node_records.append(node_record)

    if section_records:
        primary_level, secondary_level = navigation_levels(None if section == graph.document else section, section_records)
    else:
        primary_level, secondary_level = (None, None)

    order_ids = []
    order_index = {}
    for level_name in NAVIGATION_SECTION_ORDER:
        for section_id in ordered_section_ids(section, level_name, set(section_records.keys())):
            if section_id not in order_index:
                order_index[section_id] = len(order_ids)
                order_ids.append(section_id)
    for section_id in section_records:
        if section_id not in order_index:
            order_index[section_id] = len(order_ids)
            order_ids.append(section_id)

    for record in section_records.values():
        record['nodeIds'] = sorted(section_node_ids.get(record['id'], ()))
        record['childIds'] = []
    for record in section_records.values():
        parent_id = record['parentId']
        if parent_id in section_records:
            section_records[parent_id]['childIds'].append(record['id'])
    for record in section_records.values():
        record['childIds'] = sorted(record['childIds'], key=lambda item: order_index.get(item, len(order_ids)))

    primary_ids = []
    if primary_level is not None:
        primary_ids = [
            section_id for section_id in order_ids
            if section_records[section_id]['type'] == primary_level and section_records[section_id]['nodeIds']
        ]

    secondary_by_parent = {}
    if secondary_level is not None:
        for primary_id in primary_ids:
            secondary_ids = [
                section_id for section_id in section_records[primary_id]['childIds']
                if section_records[section_id]['type'] == secondary_level and section_records[section_id]['nodeIds']
            ]
            if secondary_ids:
                secondary_by_parent[primary_id] = secondary_ids

    for node_record in node_records:
        section_ids = node_record['sectionIds']
        node_record['primarySectionId'] = next(
            (section_id for section_id in section_ids if section_records[section_id]['type'] == primary_level),
            None,
        )
        node_record['secondarySectionId'] = next(
            (section_id for section_id in section_ids if section_records[section_id]['type'] == secondary_level),
            None,
        )
        node_record['chapterId'] = node_record['primarySectionId']
        node_record['subchapterId'] = node_record['secondarySectionId']

    all_node_ids = {node.id for node in graph.nodes}
    view_section_ids = list(primary_ids)
    for section_ids in secondary_by_parent.values():
        view_section_ids.extend(section_ids)
    views = {
        section_id: collapsed_view(set(section_records[section_id]['nodeIds']), all_node_ids, semantic_edge_records)
        for section_id in view_section_ids
    }

    root_record = {
        'id': 'document',
        'type': 'document',
        'level': None,
        'counter': '',
        'ref': '',
        'title': text_content(getattr(graph.document, 'userdata', {}).get('title', None)),
        'label': 'Document',
        'fullTitle': text_content(getattr(graph.document, 'userdata', {}).get('title', None)) or 'Document',
        'url': '',
        'filename': '',
        'parentId': None,
    }
    if section != graph.document:
        root_record = section_record(section)

    return {
        'root': root_record,
        'levels': {
            'primary': primary_level,
            'secondary': secondary_level,
        },
        'sections': [section_records[section_id] for section_id in order_ids if section_records[section_id]['nodeIds']],
        'primarySectionIds': primary_ids,
        'secondarySectionIdsByParent': secondary_by_parent,
        'nodes': node_records,
        'displayEdges': display_edge_records,
        'fullEdges': semantic_edge_records,
        'views': views,
    }

class DepGraph():
    """
    A TeX declarations dependency graph, built using the `\\uses and `\\proves` commands.
    """
    def __init__(self):
        self.nodes = set()
        self.edges = set()
        self.proof_edges = set()
        self.document = None
        self._ancestors = dict()
        self._predecessors = dict()

    def predecessors(self, node):
        """
        Return direct predecessors of the given node, as a set.
        This is meant to be called only after all nodes have been added since the result is cached.
        """
        if node in self._predecessors:
            return self._predecessors[node]
        else:
            return self._predecessors.setdefault(node, {e[0] for e in self.edges.union(self.proof_edges) if e[1] == node})

    def ancestors(self, node):
        """
        Return ancestors of the given node, as a set.
        This is meant to be called only after all nodes have been added since the result is cached.
        """
        if not node in self.nodes:
            return set()
        else:
            if node in self._ancestors:
                return self._ancestors[node]
            else:
                pred = self.predecessors(node)
                return self._ancestors.setdefault(node,
                                pred.union(*map(self.ancestors, pred)))


    def to_dot(self, shapes: dict) -> AGraph:
        """Convert to pygraphviz AGraph"""
        graph = AGraph(directed=True, bgcolor='transparent')
        graph.node_attr['penwidth'] = 1.8
        graph.edge_attr.update(arrowhead='vee')
        for node in self.nodes:
            color = self.document.userdata['dep_graph'].get('colorizer', lambda x: '')(node)
            fillcolor = self.document.userdata['dep_graph'].get('fillcolorizer', lambda x: '')(node)

            if fillcolor:
                style = self.document.userdata['dep_graph'].get('stylerizer', lambda x: 'filled')(node)

                graph.add_node(node.id,
                               label=node.id.split(':')[-1],
                               shape=shapes.get(item_kind(node), 'ellipse'),
                               style=style,
                               color=color,
                               fillcolor=fillcolor)
            else:
                style = self.document.userdata['dep_graph'].get('stylerizer', lambda x: '')(node)
                graph.add_node(node.id,
                               label=node.id.split(':')[-1],
                               shape=shapes.get(item_kind(node), 'ellipse'),
                               style=style,
                               color=color)
        for s, t in self.edges:
            if s in self.nodes and t in self.nodes:
                graph.add_edge(s.id, t.id, style='dashed')
        for s, t in self.proof_edges:
            if s in self.nodes and t in self.nodes:
                graph.add_edge(s.id, t.id)
        return graph

class bpcolor(Command):
    r"""\bpcolor{key}{color}{description}"""
    args = 'key:str color:str descr:str'

    def invoke(self, tex):
        Command.invoke(self, tex)
        colors = self.ownerDocument.userdata['dep_graph']['colors']
        key = self.attributes['key']
        color = self.attributes['color']
        descr = self.attributes['descr']
        if key not in colors:
            valid = ', '.join(colors.keys())
            log.error(f'Invalid dependency graph color key: {key}. Valid keys are {valid}.')
            return []
        colors[key] = (color, descr)
        return []

class uses(Command):
    r"""\uses{labels list}"""
    args = 'labels:list:nox'

    def digest(self, tokens):
        Command.digest(self, tokens)
        node = self.parentNode
        doc = self.ownerDocument
        def update_used():
            labels_dict = doc.context.labels
            used = [labels_dict[label]
                    for label in self.attributes['labels'] if label in labels_dict]
            for label in self.attributes['labels']:
                if label not in labels_dict:
                    log.error("Label '" + label + "' could not be resolved")
            node.setUserData('uses', used)

        doc.addPostParseCallbacks(10, update_used)

class alsoIn(Command):
    r"""\uses{labels list}"""
    args = 'labels:list:nox'

    def digest(self, tokens):
        Command.digest(self, tokens)
        node = self.parentNode
        doc = self.ownerDocument
        def update_incls():
            """
            Updates the doc.userdata['graph_includes'] dict.
            Each key in this dict is a section object,
            and the corresponding value is a list of nodes
            to also include in the dep graph of that section.
            """
            labels_dict = doc.context.labels
            alsoin = [labels_dict[label]
                      for label in self.attributes['labels']
                      if label in labels_dict]
            incls = doc.userdata.setdefault('graph_includes', dict())
            for decl in alsoin:
                incls.setdefault(decl, []).append(node)

        doc.addPostParseCallbacks(10, update_incls)

class proves(Command):
    r"""\proves{label}"""
    args = 'label:str'

    def digest(self, tokens):
        Command.digest(self, tokens)
        node = self.parentNode
        doc = self.ownerDocument
        def update_proved() -> None:
            labels_dict = doc.context.labels
            proved = labels_dict.get(self.attributes['label'])
            if proved:
                node.setUserData('proves', proved)
                proved.userdata['proved_by'] = node
        doc.addPostParseCallbacks(10, update_proved)


def find_proved_thm(proof) -> Optional[Environment]:
    """From a proof node, try to find the statement."""
    node = proof.parentNode
    while node.previousSibling:
        childNodes = node.previousSibling.childNodes
        if childNodes and childNodes[0].nodeName == 'thmenv':
            return childNodes[0]
        node = node.previousSibling
    return None

LINK_TPL = Template("""
    <a class="icon proof" href="{{ obj.url }}">#</a>
""")

PROVED_BY_TPL = Template("""
    {% if obj.userdata.proved_by %}
    <a class="icon proof" href="{{ obj.userdata.proved_by.url }}">{{ icon('cogs') }}</a>
    {% endif %}
""")

USES_TPL = Template("""
    {% if obj.userdata.uses %}
    <button class="modal">{{ icon('mindmap') }}</button>
    {% call modal(context.terms.get('Uses', 'Uses')) %}
        <ul class="uses">
          {% for used in obj.userdata.uses %}
          <li><a href="{{ used.url }}">{{ used.caption }} {{ used.ref }}</a></li>
          {% endfor %}
        </ul>
    {% endcall %}
    {% endif %}
""")

def ProcessOptions(options, document):
    """This is called when the package is loaded."""

    document.rendererdata.setdefault('html5', dict())
    document.userdata['dep_graph'] = dict()

    templatedir = PackageTemplateDir(path=PKG_DIR/'renderer_templates')
    document.addPackageResource(templatedir)

    jobname = document.userdata['jobname']
    outdir = document.config['files']['directory']
    outdir = string.Template(outdir).substitute({'jobname': jobname})

    def update_proofs() -> None:
        for proof in document.getElementsByTagName('proof'):
            proved = proof.userdata.setdefault('proves', find_proved_thm(proof))
            if proved:
                proved.userdata['proved_by'] = proof
    document.addPostParseCallbacks(100, update_proofs)

    ## Dep graph
    title = options.get('title', 'Dependencies')

    def makegraph(section, title:str) -> None:
        nodes = []
        for thm_type in document.userdata['dep_graph']['thm_types']:
            nodes += section.getElementsByTagName(thm_type)
        # Add nodes that used \alsoIn
        incls = document.userdata.get('graph_includes', dict())
        nodes.extend(incls.get(section, []))

        graph = DepGraph()
        graph.document = document
        graph.nodes = set(nodes)
        for node in nodes:
            used = node.userdata.get('uses', [])
            for thm in used:
                graph.edges.add((thm, node))
            proof = node.userdata.get('proved_by')
            if proof:
                used = proof.userdata.get('uses', [])
                for thm in used:
                    graph.proof_edges.add((thm, node))

        graphs = document.userdata['dep_graph'].setdefault('graphs', dict())
        graphs[section] = graph

    def makegraphs() -> None:
        dep_by = options.get('dep_by', '')
        if dep_by:
            for section in document.getElementsByTagName(dep_by):
                graph_target = 'dep_graph_' + section.counter + '_' + section.ref.textContent + '.html'
                document.rendererdata['html5']['extra_toc_items'].append({
                    'text': section.counter.capitalize() + ' ' + section.ref.textContent + ' graph',
                    'url': graph_target})
                makegraph(section, title)
        else:
            document.rendererdata['html5']['extra_toc_items'].append({'text': 'Dependency graph','url': 'dep_graph_document.html'})
            makegraph(document, title)

    document.rendererdata['html5'].setdefault('extra_toc_items', [])
    document.addPostParseCallbacks(110, makegraphs)

    default_tpl_path = PKG_DIR.parent/'templates'/'dep_graph.html'
    graph_tpl_path = Path(options.get('tpl', default_tpl_path))
    try:
        graph_tpl = Template(graph_tpl_path.read_text())
    except IOError:
        log.warning('DepGraph template read error, using default template')
        graph_tpl = Template(default_tpl_path.read_text())

    reduce_graph = not options.get('nonreducedgraph', False)

    def make_graph_html(document):
        files = []
        for sec, graph in document.userdata['dep_graph']['graphs'].items():
            if sec == document:
                name = 'document'
            else:
                name = sec.counter + '_' + sec.ref.textContent
            graph_target = 'dep_graph_' + name + '.html'
            files.append(graph_target)
            shapes = document.userdata['dep_graph'].get('shapes', {'definition': 'box'})
            dot = graph.to_dot(shapes)
            if reduce_graph:
                dot = dot.tred()
            semantic_edge_records = edge_records_from_graph(graph)
            display_edge_records = display_edge_records_from_graph(graph, shapes, reduce_graph)
            nav_payload = navigation_payload(sec, graph, semantic_edge_records, display_edge_records, shapes)
            graph_tpl.stream(graph=graph,
                             dot=dot.to_string(),
                             navigation_payload_json=json.dumps(nav_payload),
                             context=document.context,
                             title=title,
                             legend=document.userdata['dep_graph']['legend'],
                             extra_modal_links=document.userdata['dep_graph'].get('extra_modal_links_tpl', []),
                             document=document,
                             config=document.config).dump(graph_target)
        return files

    cb = PackagePreCleanupCB(data=make_graph_html)
    css = PackageCss(path=STATIC_DIR/'dep_graph.css', copy_only=True)
    js = [PackageJs(path=STATIC_DIR/name, copy_only=True)
          for name in ['d3.min.js', 'hpcc.min.js', 'd3-graphviz.js',
                       'expatlib.wasm', 'graphvizlib.wasm']]

    document.addPackageResource([cb, css] + js)


    thm_types = [thm.strip()
                 for thm in options.get('thms', DEFAULT_TYPES).split('+')]
    document.userdata['dep_graph']['thm_types'] = thm_types

    document.userdata['thm_header_extras_tpl'] = []
    document.userdata['thm_header_hidden_extras_tpl'] = [LINK_TPL,
                                                         PROVED_BY_TPL,
                                                         USES_TPL]

    document.userdata['dep_graph']['legend'] = [('Boxes', 'definitions'), ('Ellipses', 'theorems and lemmas')]
    document.userdata['dep_graph']['extra_modal_links'] = []

