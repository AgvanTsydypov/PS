"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ImgHTMLAttributes,
  type MouseEvent as ReactMouseEvent,
} from "react";

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const ZOOM_STEP = 0.0015;

type Props = ImgHTMLAttributes<HTMLImageElement>;

export default function ZoomableImage(props: Props) {
  const { onClick: _ignored, ...imgProps } = props;
  const [open, setOpen] = useState(false);
  const [scale, setScale] = useState(MIN_SCALE);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  const reset = useCallback(() => {
    setScale(MIN_SCALE);
    setTx(0);
    setTy(0);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    reset();
  }, [reset]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    const node = overlayRef.current;
    if (!node) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setScale((s) => {
        const next = s * (1 - e.deltaY * ZOOM_STEP);
        return Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
      });
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [open]);

  const onMouseDown = (e: ReactMouseEvent<HTMLImageElement>) => {
    if (scale <= MIN_SCALE) return;
    e.preventDefault();
    dragRef.current = { x: e.clientX, y: e.clientY, tx, ty };
  };

  const onMouseMove = (e: ReactMouseEvent<HTMLImageElement>) => {
    if (!dragRef.current) return;
    setTx(dragRef.current.tx + (e.clientX - dragRef.current.x));
    setTy(dragRef.current.ty + (e.clientY - dragRef.current.y));
  };

  const stopDrag = () => {
    dragRef.current = null;
  };

  const onDoubleClick = () => {
    if (scale > MIN_SCALE) {
      reset();
    } else {
      setScale(2.5);
    }
  };

  const zoomIn = () => setScale((s) => Math.min(MAX_SCALE, s * 1.4));
  const zoomOut = () =>
    setScale((s) => {
      const next = s / 1.4;
      if (next <= MIN_SCALE) {
        setTx(0);
        setTy(0);
        return MIN_SCALE;
      }
      return next;
    });

  const zoomed = scale > MIN_SCALE;

  return (
    <>
      <img
        {...imgProps}
        onClick={() => setOpen(true)}
        className={`sm-md-img-trigger${imgProps.className ? ` ${imgProps.className}` : ""}`}
      />
      {open && (
        <div
          ref={overlayRef}
          className="sm-lightbox"
          role="dialog"
          aria-modal="true"
          onClick={close}
        >
          <div className="sm-lightbox-toolbar" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="sm-lightbox-btn" onClick={zoomOut} aria-label="Zoom out">
              −
            </button>
            <span className="sm-lightbox-zoom">{Math.round(scale * 100)}%</span>
            <button type="button" className="sm-lightbox-btn" onClick={zoomIn} aria-label="Zoom in">
              +
            </button>
            <button type="button" className="sm-lightbox-btn" onClick={reset} aria-label="Reset zoom">
              ⟲
            </button>
            <button type="button" className="sm-lightbox-btn sm-lightbox-close" onClick={close} aria-label="Close">
              ×
            </button>
          </div>
          <img
            {...imgProps}
            className="sm-lightbox-img"
            style={{
              transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
              cursor: zoomed ? (dragRef.current ? "grabbing" : "grab") : "zoom-in",
            }}
            onClick={(e) => {
              e.stopPropagation();
              if (!zoomed) setScale(2.5);
            }}
            onDoubleClick={onDoubleClick}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={stopDrag}
            onMouseLeave={stopDrag}
            draggable={false}
          />
        </div>
      )}
    </>
  );
}
