"use client";

import { useEffect, useRef, useState } from "react";

const SCRAMBLE_CHARS = "!<>-_\\/[]{}—=+*^?#________";

type Props = {
  text: string;
  trigger?: number;
  duration?: number;
  className?: string;
};

export default function ScrambleText({ text, trigger = 0, duration = 700, className }: Props) {
  const [output, setOutput] = useState(text);
  const rafRef = useRef<number | null>(null);
  const firstRunRef = useRef(true);

  useEffect(() => {
    if (firstRunRef.current) {
      firstRunRef.current = false;
      setOutput(text);
      return;
    }
    const start = performance.now();
    const from = output;
    const to = text;
    const length = Math.max(from.length, to.length);
    const queue: { from: string; to: string; start: number; end: number; char?: string }[] = [];
    for (let i = 0; i < length; i++) {
      const fromChar = from[i] || "";
      const toChar = to[i] || "";
      const charStart = Math.floor(Math.random() * (duration * 0.4));
      const charEnd = charStart + Math.floor(Math.random() * (duration * 0.5)) + duration * 0.1;
      queue.push({ from: fromChar, to: toChar, start: charStart, end: charEnd });
    }

    const tick = (now: number) => {
      const elapsed = now - start;
      let result = "";
      let complete = 0;
      for (let i = 0; i < queue.length; i++) {
        const q = queue[i];
        if (elapsed >= q.end) {
          complete++;
          result += q.to;
        } else if (elapsed >= q.start) {
          if (!q.char || Math.random() < 0.28) {
            q.char = SCRAMBLE_CHARS[Math.floor(Math.random() * SCRAMBLE_CHARS.length)];
          }
          result += q.char;
        } else {
          result += q.from;
        }
      }
      setOutput(result);
      if (complete < queue.length) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        rafRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger]);

  useEffect(() => {
    if (rafRef.current === null) {
      setOutput(text);
    }
  }, [text]);

  return <span className={className}>{output}</span>;
}
