with import <nixpkgs> {};
let 
  pythonPackages = pkgs.python36Packages;
in
with pythonPackages;

buildPythonPackage rec {
  name = "backtab";
  src = ".";
  propagatedBuildInputs = [ ];
  buildInputs = with pythonPackages; [ bottle pyyaml beancount ];
}
