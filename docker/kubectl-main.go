// The same entry point as upstream's cmd/kubectl. See the Dockerfile for why it's built here.
package main

import (
	"k8s.io/component-base/cli"
	"k8s.io/kubectl/pkg/cmd"
	"k8s.io/kubectl/pkg/cmd/util"

	_ "k8s.io/client-go/plugin/pkg/client/auth"
)

func main() {
	command := cmd.NewDefaultKubectlCommand()
	if err := cli.RunNoErrOutput(command); err != nil {
		util.CheckErr(err)
	}
}
