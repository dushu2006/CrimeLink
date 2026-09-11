import "cytoscape";

declare module "cytoscape" {
  namespace Css {
    interface Edge {
      width?: any;
      "text-background-padding"?: any;
    }
  }
}
