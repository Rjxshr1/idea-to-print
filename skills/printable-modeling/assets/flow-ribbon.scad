// Editable parameterized geometry demonstration; no slice or physical print claimed.
// Millimetres. Modest, flat base with three rising, tapering spiral ribbons.
height = 150;
twist_degrees = 150;
ribbon_width = 22;
ribbon_thickness = 2.4;
radial_offset = 13;
top_scale = 0.62;
base_radius = 27;
base_height = 3;
ribbon_count = 3;
$fn = 80;
assert(height > base_height && base_height > 0);
assert(ribbon_thickness >= 1.2 && ribbon_width > ribbon_thickness);
assert(top_scale > 0 && ribbon_count >= 1 && ribbon_count <= 6);
assert(base_radius > radial_offset + ribbon_width / 2);

module ribbon_section() {
    translate([radial_offset, 0])
        offset(r=ribbon_thickness/2)
            square([ribbon_width-ribbon_thickness, 0.01], center=true);
}

color([0.94, 0.94, 0.92])
union() {
    cylinder(r=base_radius, h=base_height);
    for (i=[0:ribbon_count-1])
        rotate([0,0,i*360/ribbon_count])
            translate([0,0,base_height-0.3])
                linear_extrude(height=height-base_height+0.3,
                               twist=twist_degrees, scale=top_scale,
                               slices=180, convexity=10)
                    ribbon_section();
}
