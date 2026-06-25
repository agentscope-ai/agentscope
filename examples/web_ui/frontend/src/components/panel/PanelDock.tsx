import { X } from 'lucide-react';
import { Fragment, type ReactNode } from 'react';

import { Button } from '@/components/ui/button.tsx';
import {
	ResizableHandle,
	ResizablePanel,
	ResizablePanelGroup,
} from '@/components/ui/resizable.tsx';

/**
 * Identifier for a dockable panel. Used both as the React key and to
 * look its descriptor up in {@link PanelDockProps.panels}.
 */
export type PanelKey = 'plan' | 'mcp' | 'skill' | 'permission';

/**
 * The presentation of a single panel: header chrome plus its already
 * data-bound content node. Built by the owner (ChatViewport) so that
 * the dock itself stays free of any business/data dependency.
 */
export interface PanelDescriptor {
	/** Header title shown in the panel's chrome. */
	title: ReactNode;
	/** Optional leading icon shown next to the title. */
	icon?: ReactNode;
	/**
	 * The panel body. Constructed by the owner with live data
	 * (e.g. `<TaskPanel tasksContext={...} />`) so it always reflects
	 * the latest state on every owner re-render.
	 */
	content: ReactNode;
}

interface PanelDockProps {
	/**
	 * Controlled layout state. Outer array = columns (laid out left to
	 * right), inner array = the panels stacked top to bottom within a
	 * column (max 2). The dock never mutates this; it only renders it.
	 */
	layout: PanelKey[][];
	/**
	 * Lookup from {@link PanelKey} to its {@link PanelDescriptor}. Only
	 * needs to contain keys that may appear in {@link layout}.
	 */
	panels: Record<PanelKey, PanelDescriptor>;
	/**
	 * Invoked when a panel's close button is clicked. The owner is
	 * responsible for removing the key from {@link layout} (and dropping
	 * the column if it becomes empty).
	 *
	 * @param key - The panel being closed.
	 */
	onClosePanel: (key: PanelKey) => void;
}

/**
 * Minimum width of a dock column. Keeps a panel's content (search box,
 * list items) from being squeezed unusably narrow.
 */
const COLUMN_MIN_WIDTH = '20rem';

/**
 * Minimum height of a single panel within a column. Guarantees the
 * title bar plus a sliver of content stays visible.
 */
const PANEL_MIN_HEIGHT = '6rem';

interface PanelProps {
	title: ReactNode;
	icon?: ReactNode;
	onClose: () => void;
	children: ReactNode;
}

/**
 * Generic panel chrome: a bordered container with a title bar (icon +
 * title + close button) and a flexible body. Content components are
 * rendered as `children` and should not draw their own header/border.
 *
 * @param title - Header title.
 * @param icon - Optional leading icon.
 * @param onClose - Called when the close button is clicked.
 * @param children - The panel body.
 * @returns The panel chrome element.
 */
export const Panel = ({ title, icon, onClose, children }: PanelProps) => {
	return (
		<div className="flex flex-1 flex-col border rounded-sm h-full py-1 min-h-0">
			<div className="flex items-center justify-between px-2">
				<span className="flex items-center gap-x-1.5 text-sm">
					{icon}
					{title}
				</span>
				<Button variant="ghost" size="icon-sm" onClick={onClose}>
					<X />
				</Button>
			</div>
			<div className="flex flex-1 flex-col min-h-0 overflow-auto px-2">{children}</div>
		</div>
	);
};

/**
 * A pure layout engine for the right-hand dockable panel area.
 *
 * Knows nothing about MCP / tasks / skills — it just arranges whatever
 * panels {@link PanelDockProps.layout} describes into a grid of
 * horizontally-resizable columns, each containing up to two
 * vertically-resizable rows. The panel bodies arrive pre-built via
 * {@link PanelDockProps.panels}.
 *
 * Rendered as a sibling of the chat panel inside the outer horizontal
 * `ResizablePanelGroup`, so it emits one `ResizablePanel` per column
 * (with a `ResizableHandle` between columns) to share that group.
 *
 * @param layout - Controlled column/panel arrangement.
 * @param panels - Descriptor lookup for each panel key.
 * @param onClosePanel - Close-request callback.
 * @returns The column fragment, or `null` when no panels are open.
 */
export const PanelDock = ({ layout, panels, onClosePanel }: PanelDockProps) => {
	if (layout.length === 0) return null;

	return (
		<>
			{layout.map((column, colIndex) => (
				<Fragment key={`col-${column.join('-')}`}>
					{colIndex > 0 && <ResizableHandle withHandle className="bg-transparent" />}
					<ResizablePanel className="p-1" minSize={COLUMN_MIN_WIDTH} defaultSize="22rem">
						<ResizablePanelGroup orientation="vertical">
							{column.map((key, rowIndex) => {
								const descriptor = panels[key];
								if (!descriptor) return null;
								return (
									<Fragment key={key}>
										{rowIndex > 0 && (
											<ResizableHandle
												withHandle
												className="bg-transparent"
											/>
										)}
										<ResizablePanel className="py-1" minSize={PANEL_MIN_HEIGHT}>
											<Panel
												title={descriptor.title}
												icon={descriptor.icon}
												onClose={() => onClosePanel(key)}
											>
												{descriptor.content}
											</Panel>
										</ResizablePanel>
									</Fragment>
								);
							})}
						</ResizablePanelGroup>
					</ResizablePanel>
				</Fragment>
			))}
		</>
	);
};
