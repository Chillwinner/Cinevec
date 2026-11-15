import * as THREE from "three";

export const SLIDE_VERTEX_SHADER = /* glsl */ `
  uniform float uPanelRadius;
  uniform float uPanelOvalRatio;
  uniform float uWrapAmount;
  uniform float uWrapAxis;
  uniform float uCurve;
  uniform float uVelocity;
  uniform float uPullAmount;
  uniform vec2 uPullOrigin;

  varying vec2 vUv;
  varying vec3 vWorldPos;

  void main() {
    vUv = uv;
    vec3 pos = position;

    // 柱面弧形包裹 (借鉴 webtemp LiquidSlider 算法)
    float axisCoord = mix(pos.x, pos.y, uWrapAxis);
    float angle = axisCoord / max(uPanelRadius, 0.001);
    float sinWrapped = sin(angle) * uPanelRadius;
    float zCurve = (1.0 - cos(angle)) * uPanelRadius * uPanelOvalRatio;

    pos.x = mix(pos.x, mix(sinWrapped, pos.x, uWrapAxis), uWrapAmount);
    pos.y = mix(pos.y, mix(pos.y, sinWrapped, uWrapAxis), uWrapAmount);
    pos.z -= zCurve * uWrapAmount;

    // 柔和平面弧度修正
    pos.z -= (pos.x * pos.x + pos.y * pos.y * 0.3) * uCurve * 0.0006;

    // 滚动速度引发的液态切变
    float edgeCenter = (uv.y - 0.5) * 2.0;
    float curveFalloff = 1.0 - edgeCenter * edgeCenter;
    pos.x += uVelocity * 0.06 * curveFalloff;
    pos.y *= 1.0 - abs(uVelocity) * 0.008 * curveFalloff;

    // 鼠标悬停磁吸拉近
    float pullDist = distance(uv, uPullOrigin);
    float pullFactor = 1.0 - smoothstep(0.0, 0.9, pullDist);
    pos.z += uPullAmount * pullFactor;

    vec4 worldPosition = modelMatrix * vec4(pos, 1.0);
    vWorldPos = worldPosition.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

export const SLIDE_FRAGMENT_SHADER = /* glsl */ `
  uniform sampler2D map;
  uniform vec2 uCoverScale;
  uniform vec2 uCoverOffset;
  uniform float uVelocity;
  uniform float uDim;
  uniform float uAlpha;
  uniform float uContentAlpha;
  uniform vec3 uBgColor;
  uniform float uBgOpacity;
  uniform vec3 uLightPos;
  uniform vec3 uLightColor;
  uniform float uLightIntensity;
  uniform float uLightRadius;
  uniform float uLightFade;
  uniform float uBlackout;
  uniform float uBlackoutMixMax;
  uniform vec2 uSlideSize;
  uniform float uCornerRadius;

  varying vec2 vUv;
  varying vec3 vWorldPos;

  // 圆角矩形 SDF 裁剪
  float roundedBoxMask(vec2 uv, vec2 size, float radius) {
    vec2 p = abs(uv - 0.5) * size - (size * 0.5 - radius);
    float d = length(max(p, vec2(0.0))) + min(max(p.x, p.y), 0.0) - radius;
    return 1.0 - smoothstep(-1.0, 1.0, d);
  }

  void main() {
    vec2 coverUv = vUv * uCoverScale + uCoverOffset;
    float aberration = clamp(uVelocity * 0.0022, -0.018, 0.018);

    vec4 tex = texture2D(map, coverUv);
    vec3 col = tex.rgb;

    if (abs(aberration) > 0.0001) {
      col.r = texture2D(map, coverUv + vec2(aberration, 0.0)).r;
      col.b = texture2D(map, coverUv - vec2(aberration, 0.0)).b;
    }

    col *= uDim;

    // 鼠标影院聚光灯
    float lightDist = distance(vWorldPos, uLightPos);
    float lightFactor = 1.0 - smoothstep(0.0, max(uLightRadius, 0.001), lightDist);
    vec3 lit = col + uLightColor * lightFactor * uLightIntensity * uLightFade;

    vec3 blacked = mix(lit, vec3(0.9686), uBlackout * uBlackoutMixMax);
    float blackAlpha = mix(tex.a, 1.0, uBlackout);

    // 极微弱胶片颗粒防止暗部色带 (Dithering)
    float grain = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
    float dither = (grain - 0.5) / 255.0;

    vec3 finalRgb = mix(uBgColor, blacked, uContentAlpha) + dither;
    float finalAlpha = mix(uBgOpacity, blackAlpha, uContentAlpha) * uAlpha;
    float mask = roundedBoxMask(vUv, uSlideSize, uCornerRadius);

    gl_FragColor = vec4(finalRgb, finalAlpha * mask);
  }
`;

export function createSlideMaterial(texture: THREE.Texture, slideSize: THREE.Vector2, radiusX: number, ovalRatio: number, lightPos: THREE.Vector3, lightRadius: number) {
  return new THREE.ShaderMaterial({
    vertexShader: SLIDE_VERTEX_SHADER,
    fragmentShader: SLIDE_FRAGMENT_SHADER,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    uniforms: {
      map: { value: texture },
      uPanelRadius: { value: radiusX },
      uPanelOvalRatio: { value: ovalRatio },
      uWrapAmount: { value: 1.0 },
      uWrapAxis: { value: 0.0 },
      uCurve: { value: 0.0 },
      uVelocity: { value: 0.0 },
      uPullAmount: { value: 0.0 },
      uPullOrigin: { value: new THREE.Vector2(0.5, 0.5) },
      uCoverScale: { value: new THREE.Vector2(1.0, 1.0) },
      uCoverOffset: { value: new THREE.Vector2(0.0, 0.0) },
      uDim: { value: 1.0 },
      uAlpha: { value: 1.0 },
      uContentAlpha: { value: 0.0 },
      uBgColor: { value: new THREE.Vector3(0.06, 0.06, 0.065) },
      uBgOpacity: { value: 0.85 },
      uLightPos: { value: lightPos },
      uLightColor: { value: new THREE.Vector3(0.95, 0.92, 0.88) },
      uLightIntensity: { value: 0.14 },
      uLightRadius: { value: lightRadius },
      uLightFade: { value: 1.0 },
      uBlackout: { value: 0.0 },
      uBlackoutMixMax: { value: 0.85 },
      uSlideSize: { value: slideSize.clone() },
      uCornerRadius: { value: 10.0 },
    },
  });
}
