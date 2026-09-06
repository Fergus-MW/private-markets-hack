import * as THREE from 'three';

// Rainbow accent spectrum.
const PALETTE = ['#5a18ff', '#00adfc', '#00f8e1', '#cf00e4', '#ff00ae', '#ff8262'];

export function createGraph(container) {
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.8));
  container.append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(48, 1, .1, 100);
  camera.position.z = 26;
  const group = new THREE.Group();
  scene.add(group);
  let seed = 60;
  const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const count = innerWidth < 600 ? 105 : 180;
  const bases = [], colours = [], sizes = [];
  // Loose clusters read as relationships, rather than a uniform particle field.
  const centres = [[-13, 5, 0], [-9, -5, 2], [-3, 7, -3], [4, -6, 1], [11, 5, 0], [15, -3, -2]];
  for (let i = 0; i < count; i++) {
    const cluster = i % centres.length;
    const centre = centres[cluster];
    const angle = random() * Math.PI * 2;
    const radius = Math.sqrt(random()) * 6;
    bases.push(new THREE.Vector3(centre[0] + Math.cos(angle) * radius, centre[1] + Math.sin(angle) * radius, centre[2] + (random() - .5) * 8));
    colours.push(new THREE.Color(PALETTE[cluster]));
    sizes.push(random() > .88 ? 6.5 : 2 + random() * 2);
  }
  const positions = new Float32Array(count * 3);
  const colourArray = new Float32Array(count * 3);
  colours.forEach((colour, i) => colour.toArray(colourArray, i * 3));
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colourArray, 3));
  geometry.setAttribute('size', new THREE.Float32BufferAttribute(sizes, 1));
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, vertexColors: true, blending: THREE.AdditiveBlending,
    vertexShader: `attribute float size; varying vec3 vColor; void main() { vColor = color; vec4 p = modelViewMatrix * vec4(position, 1.0); gl_PointSize = size * (65.0 / -p.z); gl_Position = projectionMatrix * p; }`,
    fragmentShader: `varying vec3 vColor; void main() { float d = length(gl_PointCoord - .5); if(d > .5) discard; float glow = exp(-d * d * 22.0); gl_FragColor = vec4(vColor * 1.7, glow); }`,
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  group.add(points);
  const edges = [];
  for (let i = 0; i < count; i++) {
    const neighbours = bases.map((p, j) => ({ j, distance: bases[i].distanceTo(p) }))
      .filter(({ j, distance }) => j !== i && distance < 6).sort((a, b) => a.distance - b.distance).slice(0, 4);
    for (const { j } of neighbours) if (j > i) edges.push([i, j]);
  }
  const linePositions = new Float32Array(edges.length * 6);
  const lineColours = new Float32Array(edges.length * 6);
  edges.forEach(([a, b], i) => { colours[a].toArray(lineColours, i * 6); colours[b].toArray(lineColours, i * 6 + 3); });
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
  lineGeometry.setAttribute('color', new THREE.BufferAttribute(lineColours, 3));
  const lineMaterial = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .33, blending: THREE.AdditiveBlending, depthWrite: false });
  const lines = new THREE.LineSegments(lineGeometry, lineMaterial);
  lines.frustumCulled = false;
  group.add(lines);
  let paused = false, previous = 0, time = 0;
  const pointer = new THREE.Vector2();
  const onPointer = (e) => { pointer.set((e.clientX / innerWidth - .5) * .15, (e.clientY / innerHeight - .5) * .1); };
  function draw(now = 0) {
    const delta = previous ? Math.min((now - previous) / 1000, .05) : 0;
    previous = now;
    if (!paused) time += delta;
    bases.forEach((base, i) => {
      positions[i * 3] = base.x + Math.sin(time * .17 + i * 1.7) * .5;
      positions[i * 3 + 1] = base.y + Math.cos(time * .21 + i) * .45;
      positions[i * 3 + 2] = base.z + Math.sin(time * .15 + i) * .6;
    });
    edges.forEach(([a, b], i) => {
      linePositions.set(positions.subarray(a * 3, a * 3 + 3), i * 6);
      linePositions.set(positions.subarray(b * 3, b * 3 + 3), i * 6 + 3);
    });
    geometry.attributes.position.needsUpdate = true;
    lineGeometry.attributes.position.needsUpdate = true;
    if (!paused) {
      group.rotation.y += (Math.sin(time * .045) * .15 + pointer.x - group.rotation.y) * .015;
      group.rotation.x += (pointer.y - group.rotation.x) * .015;
      group.rotation.z = Math.sin(time * .035) * .035;
    }
    renderer.render(scene, camera);
  }
  function resize() {
    camera.aspect = innerWidth / innerHeight;
    camera.position.z = camera.aspect < 1 ? 32 : 26;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    draw();
  }
  function updateLoop() { previous = 0; renderer.setAnimationLoop(paused || document.hidden ? null : draw); }
  window.addEventListener('resize', resize);
  window.addEventListener('pointermove', onPointer, { passive: true });
  document.addEventListener('visibilitychange', updateLoop);
  resize(); updateLoop();
  return {
    setPaused(value) { paused = value; updateLoop(); draw(); },
    dispose() {
      renderer.setAnimationLoop(null);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onPointer);
      document.removeEventListener('visibilitychange', updateLoop);
      geometry.dispose(); lineGeometry.dispose(); material.dispose(); lineMaterial.dispose(); renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
