"""Deterministic rectangle detection and spatial clustering, without LLMs."""
from collections import Counter, defaultdict
import math
from xml.sax.saxutils import escape


def poly_vertices(entity):
    if entity.dxftype() == 'LWPOLYLINE':
        if any(abs(p[4]) > 1e-10 for p in entity.get_points()):
            return []
        points = [(float(p.x), float(p.y)) for p in entity.vertices_in_wcs()]
        closed = entity.closed
    elif entity.dxftype() == 'POLYLINE' and not entity.is_3d_polyline and not entity.is_polygon_mesh and not entity.is_poly_face_mesh:
        if any(abs(v.dxf.get('bulge', 0)) > 1e-10 for v in entity.vertices):
            return []
        points = [(float(p.x), float(p.y)) for p in entity.points_in_wcs()]
        closed = entity.is_closed
    else:
        return []
    if closed and points and points[-1] != points[0]:
        points.append(points[0])
    return points


def rectangle_from_path(points):
    """Require complete axis-aligned perimeter, including open paths that trace it."""
    if len(points) < 4:
        return None
    xs, ys = zip(*points)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if min(w, h) <= 1e-8:
        return None
    tol = max(w, h) * 1e-6
    sides = defaultdict(list)
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        if abs(ay - by) <= tol:
            side = 'bottom' if abs(ay - y0) <= tol else 'top' if abs(ay - y1) <= tol else None
            interval = sorted((ax, bx))
        elif abs(ax - bx) <= tol:
            side = 'left' if abs(ax - x0) <= tol else 'right' if abs(ax - x1) <= tol else None
            interval = sorted((ay, by))
        else:
            return None
        if side is None:
            return None
        sides[side].append(interval)
    for side, length in [('bottom', w), ('top', w), ('left', h), ('right', h)]:
        spans = sorted(sides[side])
        coverage, end = 0.0, -math.inf
        for start, stop in spans:
            coverage += max(0, stop - max(start, end))
            end = max(end, stop)
        if coverage < length - tol * 4:
            return None
    return [x0, y0, x1, y1]


def detect_rectangles(entities):
    """Polylines or exactly matching four LINE edges; no arbitrary part contours."""
    entities = list(entities)
    found = []
    horizontal, vertical = defaultdict(list), defaultdict(list)
    for entity in entities:
        kind = entity.dxftype()
        if kind in {'LWPOLYLINE', 'POLYLINE'}:
            rect = rectangle_from_path(poly_vertices(entity))
            if rect:
                found.append({'bounds': rect, 'method': 'polyline_perimeter',
                              'handles': [entity.dxf.get('handle')]})
        elif kind == 'LINE':
            a, b = entity.dxf.start, entity.dxf.end
            if abs(a.y - b.y) < 1e-6 and abs(a.x - b.x) > 1e-8:
                horizontal[(round(min(a.x, b.x), 5), round(max(a.x, b.x), 5))].append((round(a.y, 5), entity.dxf.get('handle')))
            elif abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) > 1e-8:
                vertical[(round(a.x, 5), round(min(a.y, b.y), 5), round(max(a.y, b.y), 5))].append(entity.dxf.get('handle'))
    for (x0, x1), lines in horizontal.items():
        # A group of long repeated page edges is small in the supplied data.
        for i, (y0, h0) in enumerate(lines):
            for y1, h1 in lines[i + 1:]:
                low, high = sorted((y0, y1))
                left, right = vertical.get((x0, low, high)), vertical.get((x1, low, high))
                if high > low and left and right:
                    found.append({'bounds': [x0, low, x1, high], 'method': 'four_line_edges',
                                  'handles': [h0, h1, left[0], right[0]]})
    unique = {}
    for candidate in found:
        unique.setdefault(tuple(round(v, 4) for v in candidate['bounds']), candidate)
    return sorted(unique.values(), key=lambda r: (r['bounds'][2] - r['bounds'][0]) * (r['bounds'][3] - r['bounds'][1]), reverse=True)


def cluster_points(points, epsilon, min_points=2):
    """DBSCAN with grid radius search. Noise remains explicit; no scipy dependency."""
    if epsilon <= 0:
        raise ValueError('epsilon must be positive')
    grid = defaultdict(list)
    for i, (x, y) in enumerate(points):
        grid[(math.floor(x / epsilon), math.floor(y / epsilon))].append(i)
    def neighbors(i):
        x, y = points[i]
        gx, gy = math.floor(x / epsilon), math.floor(y / epsilon)
        return [j for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for j in grid[(gx + dx, gy + dy)]
                if (x - points[j][0]) ** 2 + (y - points[j][1]) ** 2 <= epsilon ** 2]
    labels = [-2] * len(points)
    cluster_id = 0
    for i in range(len(points)):
        if labels[i] != -2:
            continue
        ns = neighbors(i)
        if len(ns) < min_points:
            labels[i] = -1
            continue
        labels[i] = cluster_id
        queue, queued = list(ns), set(ns)
        for j in queue:
            if labels[j] == -1:
                labels[j] = cluster_id
            if labels[j] != -2:
                continue
            labels[j] = cluster_id
            nearby = neighbors(j)
            if len(nearby) >= min_points:
                for k in nearby:
                    if k not in queued:
                        queued.add(k)
                        queue.append(k)
        cluster_id += 1
    return {'epsilon': epsilon, 'min_points': min_points, 'cluster_count': cluster_id,
            'noise_count': labels.count(-1), 'cluster_sizes': dict(sorted(Counter(n for n in labels if n >= 0).items())),
            'labels': labels}


def frame_svg(frames):
    """Standalone schematic of modelspace boundaries, with identity tooltips."""
    frames = [f for f in frames if f.get('bounds')]
    if not frames:
        return ''
    x0 = min(f['bounds'][0] for f in frames)
    y0 = min(f['bounds'][1] for f in frames)
    x1 = max(f['bounds'][2] for f in frames)
    y1 = max(f['bounds'][3] for f in frames)
    scale = min(1450 / max(x1 - x0, 1), 950 / max(y1 - y0, 1))
    width, height = (x1 - x0) * scale + 50, (y1 - y0) * scale + 80
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="15" y="24" font-size="16">Modelspace: {len(frames)} candidate sheets (labels = frame index)</text>']
    for f in frames:
        a, b, c, d = f['bounds']
        x, y, w, h = (a - x0) * scale + 25, (y1 - d) * scale + 50, (c - a) * scale, (d - b) * scale
        title = f"{f['id']} | {f.get('drawing_number', '')} | {f.get('sheet', '')} | {f.get('title', '')}"
        rows.append(f'<g><title>{escape(title)}</title><rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" fill="#e0f2fe" fill-opacity="0.3" stroke="#0369a1" stroke-width="0.7"/><text x="{x+1:.3f}" y="{y+7:.3f}" font-size="6">{escape(f["id"])}</text></g>')
    return '\n'.join(rows + ['</svg>'])
