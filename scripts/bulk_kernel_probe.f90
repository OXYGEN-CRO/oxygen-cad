! SPDX-License-Identifier: GPL-2.0-only
! Independent invocation of the pinned, unmodified CalculiX e_c3d routine.
! No application or production solver entry point is linked or invoked.
program oxygen_bulk_kernel_probe
  implicit none
  integer :: nodes, kind, nonlinear, q, n, i, j, points, nmethod
  integer :: mi(3), kon(10), ipkon(1), nelcon(2,1), ielmat(1,1), zeros_i(1000)
  real(8) :: co(3,10), u(0:3,10), tangent(27,4,1), stress(6,4,1)
  real(8) :: s(60,60), sm(60,60), ff(60), zeros_r(10000), elcon(0:21,1,1)
  character(8) :: family
  character(20) :: sideload(1)
  character(80) :: matname(1)
  character(81) :: tieset(3,1), sets(1)
  read(*,*) nodes,kind,nonlinear
  if ((nodes/=4.and.nodes/=10).or.(kind/=2.and.kind/=-51).or.(nonlinear/=0.and.nonlinear/=1)) stop 2
  points=1
  if(nodes==10) points=4
  co=0;u=0;tangent=0;stress=0;zeros_i=0;zeros_r=0;elcon=0
  mi=(/4,3,1/);ipkon=0;ielmat=1;nelcon(1,1)=kind;nelcon(2,1)=1
  nmethod=1;s=0;sm=0;ff=0;sideload='';matname='OXYGEN NUMERICAL PROBE';tieset='';sets=''
  do n=1,nodes
    kon(n)=n
    read(*,*) co(:,n)
  enddo
  do n=1,nodes
    read(*,*) u(1:3,n)
  enddo
  do q=1,points
    read(*,*) tangent(1:21,q,1)
  enddo
  do q=1,points
    read(*,*) stress(:,q,1)
  enddo
  family='C3D4    '
  if(nodes==10) family='C3D10   '
  call e_c3d(co,kon,family,zeros_r,zeros_r,0.d0,zeros_r,0,s,sm,ff,1,nmethod, &
    elcon,nelcon,zeros_r,zeros_i,zeros_r,zeros_i,zeros_r,ielmat,zeros_i,0,zeros_r,1, &
    zeros_r,zeros_r,zeros_i,u,(/0,nonlinear/),zeros_i,sideload,zeros_r,0,0, &
    stress,stress,0,zeros_r,zeros_i,zeros_r,zeros_i,tangent,1,1.d0,matname,mi,21, &
    0,1,0,0,0,0.d0,1.d0,1,1,0,zeros_r,1.d0,zeros_i,zeros_i,zeros_r,0, &
    zeros_i,zeros_i,zeros_r,zeros_r,1,zeros_r,zeros_r,1,ipkon,zeros_r, &
    zeros_i,zeros_r,tieset,zeros_i,zeros_i,zeros_i,0,zeros_i,zeros_r,zeros_r, &
    0,zeros_r,zeros_i,zeros_r,1,1.d0,0,sets,0,zeros_i,zeros_r,zeros_i,zeros_i,0)
  if(nmethod/=1) stop 3
  do i=1,3*nodes
    write(*,'(*(ES26.17E3,1X))') (s(min(i,j),max(i,j)),j=1,3*nodes)
  enddo
end program
