import { motion, AnimatePresence } from 'framer-motion';
import React from 'react';

interface FlipCardProps {
	visible: boolean;
	children: React.ReactNode;
	className?: string;
}

/**
 * The flip card component.
 */
export const FlipCard: React.FC<FlipCardProps> = ({ visible, children, className }) => {
	return (
		<div style={{ perspective: '800px', perspectiveOrigin: '50% 100%' }} className={className}>
			<AnimatePresence mode="wait">
				{visible && (
					<motion.div
						key="flip-card"
						initial={{ rotateX: -90, opacity: 0 }}
						animate={{ rotateX: 0, opacity: 1 }}
						exit={{ rotateX: 90, opacity: 0 }}
						transition={{
							duration: 0.45,
							ease: [0.34, 1.2, 0.64, 1],
						}}
						style={{
							transformOrigin: 'bottom center',
							transformStyle: 'preserve-3d',
							willChange: 'transform, opacity',
						}}
					>
						{children}
					</motion.div>
				)}
			</AnimatePresence>
		</div>
	);
};
