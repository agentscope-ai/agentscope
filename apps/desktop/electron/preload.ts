import { contextBridge, ipcRenderer } from 'electron';

type DesktopBackendConfig = Readonly<{
	backendUrl: string;
	authToken: string;
	userId: string;
}>;

contextBridge.exposeInMainWorld('electronAPI', {
	getBackendConfig: (): DesktopBackendConfig | null => ipcRenderer.sendSync('desktop:get-config'),
});
