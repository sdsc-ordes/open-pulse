(function () {
  const canvas = document.getElementById("constellation-canvas");
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  const MOBILE_QUERY = "(max-width: 1199px), (max-height: 760px), (pointer: coarse)";
  const MOBILE_MIN_FRAME_DELTA = 33;
  const RESIZE_DEBOUNCE_MS = 150;
  const MOBILE_SMALL_HEIGHT_DELTA = 140;
  const MOBILE_AREA_REINIT_RATIO = 0.18;
  const COARSE_POINTER_QUERY = "(pointer: coarse)";
  const mobileQuery = window.matchMedia(MOBILE_QUERY);
  const coarsePointerQuery = window.matchMedia(COARSE_POINTER_QUERY);
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const faces = ["◔ᴗ◔", "•ᴥ•", "^‿^"];
  let isMobile = mobileQuery.matches;
  let isCoarsePointer = coarsePointerQuery.matches;
  let reducedMotion = motionQuery.matches;

  let width = 0;
  let height = 0;
  let rafId = 0;
  let lastPulseAt = 0;
  let lastFrameAt = 0;
  let resizeTimeoutId = 0;

  const stars = [];
  const graphNodes = [];
  const astronauts = [];
  const pulses = [];
  let pairCache = [];

  function getProfile() {
    if (!isMobile) {
      return {
        starMin: 120,
        starMax: 260,
        starDivisor: 8200,
        nodeMin: 20,
        nodeMax: 34,
        nodeDivisor: 60000,
        astronautMin: 6,
        astronautMax: 12,
        astronautDivisor: 170000,
        pulseIntervalMs: 950,
        pulseChance: 0.09,
        pulseSpeedMin: 0.018,
        pulseSpeedMax: 0.032
      };
    }

    return {
      starMin: 70,
      starMax: 145,
      starDivisor: 12500,
      nodeMin: 10,
      nodeMax: 18,
      nodeDivisor: 98000,
      astronautMin: 3,
      astronautMax: 6,
      astronautDivisor: 260000,
      pulseIntervalMs: 1500,
      pulseChance: 0.045,
      pulseSpeedMin: 0.016,
      pulseSpeedMax: 0.026
    };
  }

  function rand(min, max) {
    return Math.random() * (max - min) + min;
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function createStar() {
    const radius = rand(0.45, 1.85);
    return {
      x: rand(0, width),
      y: rand(0, height),
      r: radius,
      baseR: radius,
      alpha: rand(0.2, 0.78),
      drift: rand(0.002, 0.011),
      direction: Math.random() > 0.5 ? 1 : -1,
      spark: 0
    };
  }

  function createNode() {
    return {
      x: rand(0, width),
      y: rand(0, height),
      vx: rand(-0.12, 0.12),
      vy: rand(-0.12, 0.12),
      radius: rand(1.4, 3.4)
    };
  }

  function createAstronaut(face) {
    return {
      face: face,
      x: rand(width * 0.1, width * 0.9),
      y: rand(height * 0.1, height * 0.9),
      vx: rand(-0.05, 0.05),
      vy: rand(-0.05, 0.05),
      radius: rand(17, 24),
      phase: rand(0, Math.PI * 2),
      beam: null,
      cooldown: Math.floor(rand(35, 120))
    };
  }

  function initializeScene() {
    const profile = getProfile();
    const area = width * height;
    const starCount = Math.max(profile.starMin, Math.min(profile.starMax, Math.round(area / profile.starDivisor)));
    const nodeCount = Math.max(profile.nodeMin, Math.min(profile.nodeMax, Math.round(area / profile.nodeDivisor)));
    const astronautCount = Math.max(
      profile.astronautMin,
      Math.min(profile.astronautMax, Math.round(area / profile.astronautDivisor))
    );

    stars.length = 0;
    graphNodes.length = 0;
    astronauts.length = 0;
    pulses.length = 0;
    pairCache = [];

    for (let i = 0; i < starCount; i += 1) {
      stars.push(createStar());
    }
    for (let i = 0; i < nodeCount; i += 1) {
      graphNodes.push(createNode());
    }
    for (let i = 0; i < astronautCount; i += 1) {
      const face = faces[i % faces.length];
      astronauts.push(createAstronaut(face));
    }
  }

  function clampBounce(node, margin) {
    if (node.x <= margin || node.x >= width - margin) {
      node.vx *= -1;
      node.x = Math.min(width - margin, Math.max(margin, node.x));
    }
    if (node.y <= margin || node.y >= height - margin) {
      node.vy *= -1;
      node.y = Math.min(height - margin, Math.max(margin, node.y));
    }
  }

  function pickTargetStar(astronaut) {
    if (stars.length === 0) {
      return null;
    }

    let target = null;
    let best = Infinity;
    const attempts = Math.min(18, stars.length);

    for (let i = 0; i < attempts; i += 1) {
      const candidate = stars[Math.floor(Math.random() * stars.length)];
      const dx = astronaut.x - candidate.x;
      const dy = astronaut.y - candidate.y;
      const distance = Math.sqrt(dx * dx + dy * dy);
      if (distance < best) {
        best = distance;
        target = candidate;
      }
    }

    return target;
  }

  function palette() {
    const light = document.documentElement.getAttribute("data-theme") === "light";
    if (light) {
      return {
        star: "30, 64, 175",
        line: "37, 99, 235",
        node: "29, 78, 216",
        aura: "191, 219, 254",
        pulse: "30, 64, 175",
        text: "15, 23, 42"
      };
    }
    return {
      star: "241, 245, 255",
      line: "96, 165, 250",
      node: "147, 197, 253",
      aura: "37, 99, 235",
      pulse: "125, 211, 252",
      text: "239, 246, 255"
    };
  }

  function updateScene() {
    const profile = getProfile();

    stars.forEach(function (star) {
      star.alpha += star.drift * star.direction;
      if (star.alpha > 0.84 || star.alpha < 0.16) {
        star.direction *= -1;
      }
      star.spark *= 0.9;
      if (star.spark < 0.01) {
        star.spark = 0;
      }
      star.r += (star.baseR - star.r) * 0.055;
    });

    graphNodes.forEach(function (node) {
      node.x += node.vx;
      node.y += node.vy;
      clampBounce(node, 4);
    });

    astronauts.forEach(function (astronaut) {
      astronaut.phase += 0.01;
      astronaut.x += astronaut.vx + Math.sin(astronaut.phase) * 0.03;
      astronaut.y += astronaut.vy + Math.cos(astronaut.phase) * 0.025;
      clampBounce(astronaut, astronaut.radius + 3);

      astronaut.cooldown -= 1;
      if (!astronaut.beam && astronaut.cooldown <= 0) {
        const target = pickTargetStar(astronaut);
        if (target) {
          astronaut.beam = {
            target: target,
            progress: 0,
            speed: rand(0.024, 0.042)
          };
          astronaut.cooldown = Math.floor(rand(75, 155));
        }
      }

      if (astronaut.beam) {
        astronaut.beam.progress += astronaut.beam.speed;
        if (astronaut.beam.progress >= 1) {
          astronaut.beam.target.spark = Math.min(1, astronaut.beam.target.spark + rand(0.72, 1));
          astronaut.beam.target.alpha = Math.min(0.95, astronaut.beam.target.alpha + 0.18);
          astronaut.beam.target.r = Math.min(2.5, astronaut.beam.target.r + rand(0.08, 0.25));
          astronaut.beam = null;
        }
      }
    });

    const now = performance.now();
    if (pairCache.length > 0 && now - lastPulseAt > profile.pulseIntervalMs && Math.random() < profile.pulseChance) {
      const pair = pairCache[Math.floor(Math.random() * pairCache.length)];
      pulses.push({
        from: pair[0],
        to: pair[1],
        progress: 0,
        speed: rand(profile.pulseSpeedMin, profile.pulseSpeedMax)
      });
      lastPulseAt = now;
    }

    for (let i = pulses.length - 1; i >= 0; i -= 1) {
      pulses[i].progress += pulses[i].speed;
      if (pulses[i].progress >= 1.02) {
        pulses.splice(i, 1);
      }
    }
  }

  function drawNebula(p) {
    const g1 = ctx.createRadialGradient(width * 0.5, height * 0.1, 10, width * 0.5, height * 0.1, width * 0.62);
    g1.addColorStop(0, "rgba(" + p.line + ",0.12)");
    g1.addColorStop(1, "rgba(" + p.line + ",0)");
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, width, height);

    const g2 = ctx.createRadialGradient(width * 0.84, height * 0.25, 8, width * 0.84, height * 0.25, width * 0.44);
    g2.addColorStop(0, "rgba(" + p.pulse + ",0.08)");
    g2.addColorStop(1, "rgba(" + p.pulse + ",0)");
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, width, height);
  }

  function drawStars(p) {
    stars.forEach(function (star) {
      const spark = star.spark || 0;
      const alpha = Math.min(0.98, star.alpha + spark * 0.55);
      const radius = star.r + spark * 1.7;

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.star + "," + alpha.toFixed(3) + ")";
      ctx.arc(star.x, star.y, radius, 0, Math.PI * 2);
      ctx.fill();

      if (spark > 0.06) {
        ctx.beginPath();
        ctx.strokeStyle = "rgba(" + p.pulse + "," + (spark * 0.46).toFixed(3) + ")";
        ctx.lineWidth = 0.7;
        ctx.arc(star.x, star.y, radius + spark * 2.2, 0, Math.PI * 2);
        ctx.stroke();
      }
    });
  }

  function drawConnections(p) {
    pairCache = [];
    const nodes = graphNodes.concat(astronauts);
    const threshold = Math.min(265, Math.max(145, width * 0.2));

    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < threshold) {
          const alpha = (1 - dist / threshold) * 0.5;
          ctx.strokeStyle = "rgba(" + p.line + "," + alpha.toFixed(3) + ")";
          ctx.lineWidth = 1.05;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();

          if (dist < threshold * 0.82) {
            pairCache.push([a, b]);
          }
        }
      }
    }
  }

  function drawPulses(p) {
    pulses.forEach(function (pulse) {
      const px = pulse.from.x + (pulse.to.x - pulse.from.x) * pulse.progress;
      const py = pulse.from.y + (pulse.to.y - pulse.from.y) * pulse.progress;
      const alpha = (1 - pulse.progress) * 0.86;

      ctx.strokeStyle = "rgba(" + p.pulse + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = 1.35;
      ctx.beginPath();
      ctx.moveTo(pulse.from.x, pulse.from.y);
      ctx.lineTo(px, py);
      ctx.stroke();

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.pulse + "," + Math.max(0.18, alpha).toFixed(3) + ")";
      ctx.arc(px, py, 2.4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawAstronautRays(p) {
    astronauts.forEach(function (astronaut) {
      if (!astronaut.beam) {
        return;
      }

      const target = astronaut.beam.target;
      const progress = astronaut.beam.progress;
      const px = astronaut.x + (target.x - astronaut.x) * progress;
      const py = astronaut.y + (target.y - astronaut.y) * progress;
      const alpha = 0.68 - progress * 0.26;

      ctx.strokeStyle = "rgba(" + p.pulse + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = 1.05;
      ctx.beginPath();
      ctx.moveTo(astronaut.x, astronaut.y);
      ctx.lineTo(px, py);
      ctx.stroke();

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.pulse + ",0.9)";
      ctx.arc(px, py, 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawNodes(p) {
    graphNodes.forEach(function (node) {
      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.node + ",0.45)";
      ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawAstronauts(p) {
    astronauts.forEach(function (astronaut) {
      const glowRadius = astronaut.radius * 1.2;

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.aura + ",0.11)";
      ctx.arc(astronaut.x, astronaut.y, glowRadius, 0, Math.PI * 2);
      ctx.fill();

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.node + ",0.22)";
      ctx.arc(astronaut.x, astronaut.y, astronaut.radius, 0, Math.PI * 2);
      ctx.fill();

      ctx.beginPath();
      ctx.strokeStyle = "rgba(" + p.text + ",0.35)";
      ctx.lineWidth = 1.05;
      ctx.arc(astronaut.x, astronaut.y, astronaut.radius * 0.92, Math.PI * 0.08, Math.PI * 1.92);
      ctx.stroke();

      ctx.beginPath();
      ctx.fillStyle = "rgba(" + p.text + ",0.09)";
      ctx.ellipse(
        astronaut.x,
        astronaut.y - astronaut.radius * 0.05,
        astronaut.radius * 0.56,
        astronaut.radius * 0.43,
        0,
        0,
        Math.PI * 2
      );
      ctx.fill();

      ctx.beginPath();
      ctx.strokeStyle = "rgba(" + p.text + ",0.24)";
      ctx.lineWidth = 0.75;
      ctx.ellipse(
        astronaut.x,
        astronaut.y - astronaut.radius * 0.05,
        astronaut.radius * 0.56,
        astronaut.radius * 0.43,
        0,
        0,
        Math.PI * 2
      );
      ctx.stroke();

      ctx.beginPath();
      ctx.strokeStyle = "rgba(" + p.text + ",0.2)";
      ctx.lineWidth = 0.62;
      ctx.arc(
        astronaut.x - astronaut.radius * 0.24,
        astronaut.y - astronaut.radius * 0.35,
        astronaut.radius * 0.22,
        Math.PI * 1.1,
        Math.PI * 1.9
      );
      ctx.stroke();

      ctx.font = "10.5px 'JetBrains Mono', monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "rgba(" + p.text + ",0.6)";
      ctx.fillText(astronaut.face, astronaut.x, astronaut.y + 0.3);
    });
  }

  function draw() {
    const p = palette();
    ctx.clearRect(0, 0, width, height);
    drawNebula(p);
    drawStars(p);
    drawConnections(p);
    drawAstronautRays(p);
    drawPulses(p);
    drawNodes(p);
    drawAstronauts(p);
  }

  function animate(now) {
    const frameNow = typeof now === "number" ? now : performance.now();

    if (isCoarsePointer && frameNow - lastFrameAt < MOBILE_MIN_FRAME_DELTA) {
      rafId = window.requestAnimationFrame(animate);
      return;
    }
    lastFrameAt = frameNow;

    if (!reducedMotion) {
      updateScene();
    }
    draw();
    rafId = window.requestAnimationFrame(animate);
  }

  function restartLoop() {
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }

    if (reducedMotion) {
      draw();
      return;
    }

    lastFrameAt = 0;
    rafId = window.requestAnimationFrame(animate);
  }

  function handleCanvasResize() {
    const prevWidth = width;
    const prevHeight = height;
    const prevArea = Math.max(1, prevWidth * prevHeight);

    resizeCanvas();

    if (!isMobile) {
      initializeScene();
      restartLoop();
      return;
    }

    const widthChanged = prevWidth !== width;
    const heightDelta = Math.abs(prevHeight - height);
    const areaDeltaRatio = Math.abs(width * height - prevArea) / prevArea;
    const shouldSkipReinit =
      !widthChanged && heightDelta <= MOBILE_SMALL_HEIGHT_DELTA && areaDeltaRatio <= MOBILE_AREA_REINIT_RATIO;

    if (!shouldSkipReinit) {
      initializeScene();
    }

    restartLoop();
  }

  function scheduleResize() {
    if (resizeTimeoutId) {
      window.clearTimeout(resizeTimeoutId);
    }
    resizeTimeoutId = window.setTimeout(function () {
      resizeTimeoutId = 0;
      handleCanvasResize();
    }, RESIZE_DEBOUNCE_MS);
  }

  resizeCanvas();
  initializeScene();
  restartLoop();

  window.addEventListener("resize", scheduleResize, { passive: true });

  const handleMobileModeChange = function (event) {
    const nextIsMobile = event.matches;
    if (nextIsMobile === isMobile) {
      return;
    }
    isMobile = nextIsMobile;
    handleCanvasResize();
  };

  const handleCoarsePointerChange = function (event) {
    const nextIsCoarse = event.matches;
    if (nextIsCoarse === isCoarsePointer) {
      return;
    }
    isCoarsePointer = nextIsCoarse;
    restartLoop();
  };

  window.addEventListener("openpulse:themechange", function () {
    draw();
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (rafId) {
        window.cancelAnimationFrame(rafId);
        rafId = 0;
      }
      if (resizeTimeoutId) {
        window.clearTimeout(resizeTimeoutId);
        resizeTimeoutId = 0;
      }
      return;
    }
    restartLoop();
  });

  const handleMotionChange = function (event) {
    reducedMotion = event.matches;
    restartLoop();
  };

  if (typeof motionQuery.addEventListener === "function") {
    motionQuery.addEventListener("change", handleMotionChange);
  } else if (typeof motionQuery.addListener === "function") {
    motionQuery.addListener(handleMotionChange);
  }

  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", handleMobileModeChange);
  } else if (typeof mobileQuery.addListener === "function") {
    mobileQuery.addListener(handleMobileModeChange);
  }

  if (typeof coarsePointerQuery.addEventListener === "function") {
    coarsePointerQuery.addEventListener("change", handleCoarsePointerChange);
  } else if (typeof coarsePointerQuery.addListener === "function") {
    coarsePointerQuery.addListener(handleCoarsePointerChange);
  }
})();
