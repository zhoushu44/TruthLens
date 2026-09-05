import { createContext, useContext, useState, useId } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

const HighlightContext = createContext<any>(null);

export function Highlight({
  children,
  enabled = true,
  hover = true,
  controlledItems = false,
  mode = "parent",
  containerClassName,
  transition = { type: "spring", stiffness: 500, damping: 35 },
  forceUpdateBounds,
  ...props
}: any) {
  const [activeItem, setActiveItem] = useState<string | null>(null);
  const layoutId = "highlight-layout-" + useId();

  return (
    <HighlightContext.Provider value={{ activeItem, setActiveItem, enabled, hover, layoutId, transition }}>
      <div 
        className={cn("relative", containerClassName)} 
        onMouseLeave={() => hover && setActiveItem(null)}
        {...props}
      >
        {children}
      </div>
    </HighlightContext.Provider>
  );
}

export function HighlightItem({ children, activeClassName, isActive, ...props }: any) {
  const context = useContext(HighlightContext) || {};
  const { activeItem, setActiveItem, enabled, hover, layoutId, transition } = context;
  
  const itemId = useId();

  const isCurrentlyActive = hover ? (activeItem === itemId) : isActive;

  return (
    <div
      className="relative w-full h-full"
      onMouseEnter={() => hover && setActiveItem(itemId)}
      {...props}
    >
      <AnimatePresence>
        {enabled && isCurrentlyActive && (
          <motion.div
            layoutId={layoutId}
            className={cn("absolute inset-0 -z-10", activeClassName)}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={transition}
          />
        )}
      </AnimatePresence>
      <div className="relative z-10 w-full h-full flex flex-col items-start justify-center">
        {children}
      </div>
    </div>
  );
}
