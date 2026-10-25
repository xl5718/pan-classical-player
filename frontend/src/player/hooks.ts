import { useSyncExternalStore } from "react";
import { player } from "./engine";

export const usePlayer = () => useSyncExternalStore(player.subscribe, player.getState);
export const usePosition = () => useSyncExternalStore(player.subscribePos, player.getPosition);
